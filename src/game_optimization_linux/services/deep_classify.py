"""Deterministic, read-only classification rules for the deep analysis.

Rules never open archives and never infer a language from a substring: a
language must be a whole path segment, a whole file-name token, or come from
parsed engine metadata (Wwise ``SoundbanksInfo``).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
import re
from typing import Any, Mapping
import xml.etree.ElementTree as ElementTree

from .btrfs_analysis import (
    _EXECUTABLE_EXTENSIONS,
    _PRECOMPRESSED_EXTENSIONS,
    _TEXT_EXTENSIONS,
)

# Engine-specific rules need at least this detector confidence.
ENGINE_RULE_MIN_CONFIDENCE = 0.55
METADATA_MAX_BYTES = 8 * 1024 * 1024
OLD_FILE_SECONDS = 7 * 24 * 3600
WWISE_METADATA_NAMES = frozenset({"soundbanksinfo.xml", "soundbanksinfo.json"})

LANGUAGE_NAMES: Mapping[str, str] = {
    "english": "en", "polish": "pl", "german": "de", "french": "fr",
    "spanish": "es", "italian": "it", "portuguese": "pt", "brazilian": "pt",
    "russian": "ru", "japanese": "ja", "korean": "ko", "koreana": "ko",
    "chinese": "zh", "schinese": "zh", "tchinese": "zh", "czech": "cs",
    "hungarian": "hu", "turkish": "tr", "dutch": "nl", "swedish": "sv",
    "danish": "da", "finnish": "fi", "norwegian": "no", "ukrainian": "uk",
    "arabic": "ar", "thai": "th", "romanian": "ro", "greek": "el",
    "bulgarian": "bg", "vietnamese": "vi", "indonesian": "id", "latam": "es",
    "polski": "pl", "deutsch": "de", "francais": "fr", "espanol": "es",
    "italiano": "it", "portugues": "pt",
}
LANGUAGE_CODES = frozenset(LANGUAGE_NAMES.values())
# Codes that are also ordinary words/abbreviations need a localization context.
AMBIGUOUS_CODES = frozenset({"it", "no", "id"})
_REGION = re.compile(r"^([a-z]{2})[-_]([a-z]{2}|[0-9]{3}|hans|hant|latn|cyrl)$")
_WWISE_LANGUAGE = re.compile(r"^([a-z]+)\s*\(([^)]*)\)$")
_TOKEN_SPLIT = re.compile(r"[._\-\s]+")

LOCALIZATION_HINTS = frozenset({
    "localization", "localisation", "locale", "locales", "lang", "langs",
    "language", "languages", "loc", "l10n", "i18n", "text", "texts", "strings",
    "translations", "subtitles",
})
AUDIO_HINTS = frozenset({
    "audio", "sound", "sounds", "sfx", "voice", "voices", "vo", "dialogue",
    "dialog", "speech", "music", "wwise", "fmod", "soundbanks",
    "generatedsoundbanks", "banks",
})
PROTECTED_SEGMENTS = frozenset({
    "save", "saves", "savegame", "savegames", "savedata", "saved games",
    "profiles", "profile", "config", "configs", "settings", "userdata",
    "user", "users", "screenshots", "cloud", "remote",
})
CRASH_DIRECTORIES = frozenset({
    "crashes", "crashdumps", "crash_dumps", "crashreports", "crashreport",
    "minidumps",
})
LOG_DIRECTORIES = frozenset({"logs", "log"})
SHADER_CACHE_DIRECTORIES = frozenset({
    "shadercache", "shader_cache", "shaderscache", "shaders_cache", "d3dscache",
    "dxcache", "glcache", "nv_cache", "pipelinecache", "psocache",
})
ENGINE_CACHE_DIRECTORIES = frozenset({"deriveddatacache"})
TEMP_DIRECTORIES = frozenset({"temp", "tmp"})
REDIST_DIRECTORIES = frozenset({
    "_commonredist", "commonredist", "redist", "redistributable",
    "redistributables", "__installer", "installer", "installers", "directx",
    "vcredist", "dotnetfx",
})
AUDIO_EXTENSIONS = frozenset({
    ".pck", ".bnk", ".wem", ".bank", ".fsb", ".ogg", ".wav", ".mp3", ".opus",
    ".xwm", ".xma", ".wma", ".flac", ".aac", ".m4a",
})
CRASH_EXTENSIONS = frozenset({".dmp", ".mdmp"})
SHADER_CACHE_EXTENSIONS = frozenset({".dxvk-cache"})
BACKUP_SUFFIXES = (".bak", ".old", ".orig", ".backup")
TEXTURE_EXTENSIONS = frozenset({
    ".dds", ".ktx", ".ktx2", ".astc", ".basis", ".crn", ".vtf", ".pvr",
    ".tex", ".texture",
})
# Opaque engine containers: never opened, never modified, always core data.
PACKAGE_EXTENSIONS = frozenset({
    ".pak", ".ucas", ".utoc", ".vpk", ".bundle", ".assets", ".ress",
    ".resource", ".forge", ".big", ".rpf", ".wad", ".archive", ".arc",
    ".xp3", ".cpk", ".psarc",
})
PACKAGE_NAMES = frozenset({"globalgamemanagers", "resources.assets"})
# Usually already compressed: Btrfs transparent compression saves little.
PRECOMPRESSED_EXTENSIONS = frozenset(_PRECOMPRESSED_EXTENSIONS | {
    ".opus", ".aac", ".bik", ".bk2", ".webp", ".dds", ".ktx", ".ktx2",
    ".ucas", ".utoc", ".bundle", ".cpk", ".psarc", ".zst", ".lz4", ".pck",
    ".bnk", ".fsb", ".xwm", ".xma", ".astc", ".basis", ".crn",
})
COMPRESSIBLE_EXTENSIONS = frozenset(
    (_TEXT_EXTENSIONS | _EXECUTABLE_EXTENSIONS | {".wav", ".po", ".locres", ".pdb"})
    - {""}
)


def segment_language(segment: str) -> tuple[str, str] | None:
    """Language of one whole segment: ``(code, kind)``; kind name/region/code."""

    value = segment.strip().casefold()
    if not value:
        return None
    wwise = _WWISE_LANGUAGE.fullmatch(value)
    if wwise and wwise.group(1) in LANGUAGE_NAMES:
        return LANGUAGE_NAMES[wwise.group(1)], "name"
    if value in LANGUAGE_NAMES:
        return LANGUAGE_NAMES[value], "name"
    region = _REGION.fullmatch(value)
    if region and region.group(1) in LANGUAGE_CODES:
        return region.group(1), "region"
    if value in LANGUAGE_CODES:
        return value, "code"
    return None


def file_name_language(name: str, *, localized_context: bool) -> tuple[str, float, str] | None:
    """Language from whole file-name tokens, never from substrings."""

    stem = name.rsplit(".", 1)[0] if "." in name[1:] else name
    tokens = [token for token in _TOKEN_SPLIT.split(stem.casefold()) if token]
    for index, token in enumerate(tokens):
        if len(token) >= 4 and token in LANGUAGE_NAMES:
            return LANGUAGE_NAMES[token], 0.85, f"file name token '{token}'"
        if index + 1 < len(tokens):
            pair = f"{token}_{tokens[index + 1]}"
            region = _REGION.fullmatch(pair)
            if region and region.group(1) in LANGUAGE_CODES:
                return region.group(1), 0.85, f"file name locale '{pair}'"
    if len(tokens) >= 2:
        last = tokens[-1]
        if last in LANGUAGE_CODES and (last not in AMBIGUOUS_CODES or localized_context):
            return last, 0.75 if localized_context else 0.7, f"file name language code '{last}'"
    return None


@dataclass(frozen=True, slots=True)
class WwiseIndex:
    """Bank/stream path -> language code or ``""`` for shared (SFX) banks."""

    base: str
    by_path: Mapping[str, str]
    by_name: Mapping[str, str]

    def lookup(self, relative: str, name: str) -> str | None:
        inner = relative[len(self.base) + 1:] if self.base else relative
        folded = inner.casefold()
        if folded in self.by_path:
            return self.by_path[folded]
        return self.by_name.get(name.casefold())


def _wwise_language(raw: str) -> str:
    value = str(raw or "").strip()
    if not value or value.casefold() == "sfx":
        return ""
    found = segment_language(value) or segment_language(value.split("(")[0])
    return found[0] if found else "?"


def parse_wwise_metadata(data: bytes, name: str, base: str) -> WwiseIndex | None:
    """Parse SoundbanksInfo.xml/.json without entities and without new deps."""

    if len(data) > METADATA_MAX_BYTES:
        return None
    entries: list[tuple[str, str]] = []
    try:
        if name.casefold().endswith(".json"):
            payload = json.loads(data.decode("utf-8-sig"))
            info = payload.get("SoundBanksInfo") if isinstance(payload, Mapping) else None
            if not isinstance(info, Mapping):
                return None
            for key in ("SoundBanks", "StreamedFiles", "MediaFilesNotInAnyBank"):
                for item in info.get(key) or ():
                    if isinstance(item, Mapping) and item.get("Path"):
                        entries.append((str(item["Path"]), str(item.get("Language", ""))))
        else:
            if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
                return None  # never expand XML entities
            tree = ElementTree.fromstring(data)
            for element in tree.iter():
                if element.tag not in {"SoundBank", "File"}:
                    continue
                path = element.findtext("Path") or ""
                if path:
                    entries.append((path, element.get("Language", "")))
    except (ValueError, ElementTree.ParseError, UnicodeDecodeError, AttributeError, TypeError):
        return None
    if not entries:
        return None
    by_path: dict[str, str] = {}
    by_name: dict[str, str] = {}
    for path, language in entries:
        normalized = path.replace("\\", "/").strip("/")
        if normalized.startswith("/") or ".." in normalized.split("/"):
            continue
        code = _wwise_language(language)
        by_path[normalized.casefold()] = code
        by_name.setdefault(normalized.rsplit("/", 1)[-1].casefold(), code)
    return WwiseIndex(base, by_path, by_name)


@dataclass(frozen=True, slots=True)
class DirectoryContext:
    language: str = ""
    language_confidence: float = 0.0
    language_evidence: str = ""
    localization: bool = False
    audio: bool = False
    protected: bool = False
    redist: bool = False
    cache_kind: str = ""
    hd_texture_pack: bool = False
    temp: bool = False
    wwise: WwiseIndex | None = None
    engine: str = "Unknown"
    engine_confidence: float = 0.0


def _squashed(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def is_hd_texture_pack(segment: str) -> bool:
    squashed = _squashed(segment)
    tokens = set(_TOKEN_SPLIT.split(segment.casefold()))
    return (
        any(marker in squashed for marker in ("hdtexture", "highrestexture", "highresolutiontexture"))
        or ({"hd"} <= tokens and bool({"texture", "textures"} & tokens))
    )


def child_context(
    parent: DirectoryContext, name: str, sibling_directories: tuple[str, ...]
) -> DirectoryContext:
    folded = name.casefold()
    changes: dict[str, Any] = {}
    if folded in LOCALIZATION_HINTS:
        changes["localization"] = True
    if folded in AUDIO_HINTS:
        changes["audio"] = True
    if folded in PROTECTED_SEGMENTS:
        changes["protected"] = True
    if folded in REDIST_DIRECTORIES:
        changes["redist"] = True
    if folded in TEMP_DIRECTORIES:
        changes["temp"] = True
    unreal = parent.engine == "Unreal Engine" and parent.engine_confidence >= ENGINE_RULE_MIN_CONFIDENCE
    if folded in CRASH_DIRECTORIES:
        changes["cache_kind"] = "crash"
    elif folded in LOG_DIRECTORIES:
        changes["cache_kind"] = "unreal_logs" if unreal else "logs"
    elif folded in SHADER_CACHE_DIRECTORIES:
        changes["cache_kind"] = "shader"
    elif folded in ENGINE_CACHE_DIRECTORIES:
        changes["cache_kind"] = "engine_cache"
    if is_hd_texture_pack(name):
        changes["hd_texture_pack"] = True
    if not parent.language:
        found = segment_language(name)
        if found is not None:
            code, kind = found
            other_languages = sum(
                1 for sibling in sibling_directories
                if sibling != name and segment_language(sibling) is not None
            )
            context = parent.localization or parent.audio or bool(changes.get("audio"))
            if kind in {"name", "region"}:
                changes.update(language=code, language_confidence=0.9,
                               language_evidence=f"language directory '{name}'")
            elif (context or other_languages >= 2) and (code not in AMBIGUOUS_CODES or context):
                changes.update(language=code, language_confidence=0.85,
                               language_evidence=f"language directory '{name}' next to other languages"
                               if other_languages >= 2 else f"language directory '{name}' in a localization folder")
    return replace(parent, **changes) if changes else parent


@dataclass(frozen=True, slots=True)
class FileDecision:
    category: str
    key: str
    safety: str
    action: str
    confidence: float
    reversible: bool
    reason: str
    language: str = ""
    evidence: str = ""
    warning: str = ""


def extension_of(name: str) -> str:
    folded = name.casefold()
    if folded.endswith(".dxvk-cache"):
        return ".dxvk-cache"
    index = folded.rfind(".")
    return folded[index:] if index > 0 else ""


def compressibility(name: str) -> str:
    extension = extension_of(name)
    if extension in PRECOMPRESSED_EXTENSIONS or name.casefold() in PACKAGE_NAMES:
        return "precompressed"
    if extension in COMPRESSIBLE_EXTENSIONS:
        return "compressible"
    return "unknown_compressibility"


def classify_file(
    context: DirectoryContext,
    relative: str,
    name: str,
    *,
    size: int,
    age_seconds: float,
    sibling_names: frozenset[str],
    launcher_restores: bool,
) -> FileDecision | None:
    """Primary content decision for one file, or ``None`` (only Btrfs view)."""

    del size
    extension = extension_of(name)
    folded = name.casefold()
    old = age_seconds >= OLD_FILE_SECONDS
    if context.protected:
        if folded.endswith(BACKUP_SUFFIXES):
            return FileDecision("residues", "protected_backups", "unsupported", "keep", 0.9, False,
                                "Backup copy inside a save/config folder; it may be the only copy of user data",
                                evidence=relative)
        return None
    if context.wwise is not None and extension in AUDIO_EXTENSIONS:
        language = context.wwise.lookup(relative, name)
        if language == "":
            return FileDecision("audio", "wwise_shared", "unsupported", "keep", 0.95, False,
                                "Wwise metadata marks this bank as shared (SFX); never remove",
                                evidence="SoundbanksInfo: Language=SFX")
        if language and language != "?":
            return FileDecision("audio", f"lang:{language}", "review", "remove_candidate", 0.95,
                                launcher_restores, "Wwise metadata assigns this bank to one language",
                                language=language, evidence=f"SoundbanksInfo: language {language}")
    if context.cache_kind == "crash" or extension in CRASH_EXTENSIONS:
        return FileDecision("cache", "crash_dumps", "safe" if age_seconds >= 24 * 3600 else "review",
                            "remove_candidate", 0.9, False,
                            "Crash dump or crash report; only used for debugging a past crash",
                            evidence=relative)
    if context.cache_kind in {"logs", "unreal_logs"} or extension == ".log":
        in_log_directory = context.cache_kind in {"logs", "unreal_logs"}
        if extension in {".log", ".txt"} and in_log_directory and old:
            confidence = 0.9 if context.cache_kind == "unreal_logs" else 0.8
            return FileDecision("cache", "logs_old", "safe", "remove_candidate", confidence, False,
                                "Old log file in a log folder" + (
                                    " (Unreal Saved/Logs)" if context.cache_kind == "unreal_logs" else ""),
                                evidence=relative)
        return FileDecision("cache", "logs_recent", "review", "remove_candidate", 0.6, False,
                            "Recent or unconfirmed log file; the game may still write it",
                            evidence=relative)
    if context.cache_kind == "shader" or extension in SHADER_CACHE_EXTENSIONS:
        return FileDecision("cache", "shader_cache_installed", "review", "remove_candidate", 0.85, True,
                            "Shader cache of an installed game; it is rebuilt while playing",
                            evidence=relative,
                            warning="Deleting it causes shader-compilation stutter until it is rebuilt")
    if context.cache_kind == "engine_cache":
        return FileDecision("cache", "engine_cache", "review", "remove_candidate", 0.75, True,
                            "Engine derived-data cache; it is regenerated when needed",
                            evidence=relative, warning="The next start may be slower while the cache is rebuilt")
    if context.redist:
        return FileDecision("residues", "redistributables", "review", "remove_candidate", 0.85,
                            launcher_restores, "Redistributable installer shipped with the game",
                            evidence=relative,
                            warning="Some launchers run these installers again on the next start")
    if context.hd_texture_pack or is_hd_texture_pack(name):
        return FileDecision("textures", "hd_texture_pack", "review", "remove_candidate", 0.75,
                            launcher_restores, "Separate HD texture pack; remove only through the launcher/DLC settings",
                            evidence=relative, warning="Optional DLC content; never removed automatically")
    language = context.language
    confidence = context.language_confidence
    evidence = context.language_evidence
    if not language:
        found = file_name_language(name, localized_context=context.localization or context.audio)
        if found is not None:
            language, confidence, evidence = found
    if language:
        category = "audio" if extension in AUDIO_EXTENSIONS or context.audio else "languages"
        package = " (package kept closed; the whole file is language-specific by name)" if (
            extension in PACKAGE_EXTENSIONS) else ""
        return FileDecision(category, f"lang:{language}", "review", "remove_candidate", confidence,
                            launcher_restores, f"Language-specific data: {evidence}{package}",
                            language=language, evidence=evidence)
    if extension in AUDIO_EXTENSIONS:
        return FileDecision("audio", "shared", "unsupported", "keep", 0.8, False,
                            "Audio without a language marker is treated as shared/neutral; never remove",
                            evidence="no language segment")
    if folded.endswith(BACKUP_SUFFIXES):
        original = next((folded[: -len(suffix)] for suffix in BACKUP_SUFFIXES if folded.endswith(suffix)), "")
        if original and original in sibling_names:
            return FileDecision("residues", "backup_copies", "review", "remove_candidate", 0.7, False,
                                "Backup copy of a file that still exists next to it",
                                evidence=f"original '{original}' is present")
        return None
    if extension == ".tmp" and context.temp:
        return FileDecision("residues", "temporary_files", "review", "remove_candidate", 0.6, False,
                            "Temporary file in a temp folder", evidence=relative)
    if extension in PACKAGE_EXTENSIONS or folded in PACKAGE_NAMES:
        return FileDecision("textures", "packages_opaque", "unsupported", "keep", 0.95, False,
                            "Opaque engine archive (core data); it is never opened or modified",
                            evidence=f"extension {extension or folded}")
    if extension in TEXTURE_EXTENSIONS:
        return FileDecision("textures", "texture_files", "unsupported", "info", 0.9, False,
                            "Texture data required by the game, including mipmaps and lower quality levels",
                            evidence=f"extension {extension}")
    return None


@dataclass
class LanguageTotals:
    files: int = 0
    bytes: int = 0
    categories: set[str] = field(default_factory=set)


__all__ = [
    "AMBIGUOUS_CODES",
    "DirectoryContext",
    "ENGINE_RULE_MIN_CONFIDENCE",
    "FileDecision",
    "LANGUAGE_CODES",
    "LANGUAGE_NAMES",
    "METADATA_MAX_BYTES",
    "WWISE_METADATA_NAMES",
    "WwiseIndex",
    "child_context",
    "classify_file",
    "compressibility",
    "file_name_language",
    "is_hd_texture_pack",
    "parse_wwise_metadata",
    "segment_language",
]
