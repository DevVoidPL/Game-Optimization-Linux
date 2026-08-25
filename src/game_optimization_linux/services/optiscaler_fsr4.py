"""OptiScaler FSR4 capabilities, managed INI editing, and recommendations.

The installed ``OptiScaler.ini`` is the capability authority.  OptiScaler has
changed key names and backend names across releases, so version strings alone
must not make a control appear supported.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping, Sequence


_SECTION_RE = re.compile(r"^\s*\[([^]]+)]\s*(?:[;#].*)?$")
_SETTING_RE = re.compile(r"^(\s*)([^=;#]+?)(\s*=\s*)(.*)$")
_AVAILABLE_VALUES_RE = re.compile(
    r"(?i)available\s+values?\s*:\s*(.+)$"
)
_VALUE_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_KNOWN_UPSCALER_VALUES = frozenset(
    {
        "auto",
        "dlss",
        "xess",
        "xess_12",
        "fsr21",
        "fsr22",
        "fsr31",
        "fsr21_12",
        "fsr22_12",
        "fsr31_12",
        "ffx",
        "ffx_12",
    }
)

FSR_SECTION = "FSR"
UPSCALERS_SECTION = "Upscalers"
MANAGED_FSR_KEYS = (
    "Fsr4Update",
    "Fsr4ForceEnableInt8",
    "Fsr4ForceModel",
    "FsrAgilitySDKUpgrade",
    "Fsr4EnableWatermark",
)
MANAGED_UPSCALER_KEYS = (
    "Dx11Upscaler",
    "Dx12Upscaler",
    "VulkanUpscaler",
)
AUTO_FALSE_FSR_KEYS = frozenset(
    {
        "fsr4enablewatermark",
        "fsragilitysdkupgrade",
    }
)


@dataclass(frozen=True, slots=True)
class OptiScalerIniCapabilities:
    fsr4_update: bool
    force_int8_style: str
    agility_sdk_upgrade: bool
    watermark: bool
    dx11_values: tuple[str, ...]
    dx12_values: tuple[str, ...]
    vulkan_values: tuple[str, ...]

    @property
    def supports_fsr4(self) -> bool:
        # v0.9.4 exposes Fsr4Update.  Newer INI generations removed that key
        # and expose Fsr4ForceModel as their FSR4 control instead.
        return self.fsr4_update or self.force_int8_style == "model"

    @property
    def supports_force_int8(self) -> bool:
        return self.force_int8_style in {"boolean", "model"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "fsr4Update": self.fsr4_update,
            "forceInt8": self.supports_force_int8,
            "forceInt8Style": self.force_int8_style,
            "agilitySdkUpgrade": self.agility_sdk_upgrade,
            "watermark": self.watermark,
            "dx11Upscalers": list(self.dx11_values),
            "dx12Upscalers": list(self.dx12_values),
            "vulkanUpscalers": list(self.vulkan_values),
        }


@dataclass(frozen=True, slots=True)
class Fsr4Recommendation:
    capability: str
    recommended_mode: str
    label: str
    reason: str
    force_int8_available: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "recommendedMode": self.recommended_mode,
            "label": self.label,
            "reason": self.reason,
            "forceInt8Available": self.force_int8_available,
            "experimental": self.recommended_mode == "force_int8",
        }


@dataclass(frozen=True, slots=True)
class OptiScalerIniState:
    """Effective values read from the INI beside the injected proxy DLL."""

    fsr4_mode: str = "unknown"
    watermark: str = "unknown"
    agility_sdk_upgrade: str = "unknown"
    dx11_upscaler: str = ""
    dx12_upscaler: str = ""
    vulkan_upscaler: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "fsr4Mode": self.fsr4_mode,
            "watermark": self.watermark,
            "agilitySdkUpgrade": self.agility_sdk_upgrade,
            "dx11Upscaler": self.dx11_upscaler,
            "dx12Upscaler": self.dx12_upscaler,
            "vulkanUpscaler": self.vulkan_upscaler,
        }


def _ini_entries(text: str) -> tuple[dict[tuple[str, str], str], dict[tuple[str, str], tuple[str, ...]]]:
    section = ""
    values: dict[tuple[str, str], str] = {}
    choices: dict[tuple[str, str], tuple[str, ...]] = {}
    recent_comments: list[str] = []
    for line in text.splitlines():
        section_match = _SECTION_RE.match(line)
        if section_match:
            section = section_match.group(1).strip().casefold()
            recent_comments.clear()
            continue
        stripped = line.strip()
        # Upstream separates many setting descriptions from their key with a
        # blank line.  Keep the bounded comment window across those blanks.
        if not stripped:
            continue
        if stripped.startswith(("#", ";")):
            recent_comments.append(stripped[1:].strip())
            recent_comments = recent_comments[-6:]
            continue
        setting_match = _SETTING_RE.match(line)
        if setting_match:
            key = setting_match.group(2).strip().casefold()
            identity = (section, key)
            values[identity] = setting_match.group(4).strip()
            advertised: list[str] = []
            for comment in recent_comments:
                match = _AVAILABLE_VALUES_RE.search(comment)
                candidate_text = match.group(1) if match else comment
                for token in _VALUE_TOKEN_RE.findall(candidate_text):
                    folded = token.casefold()
                    if (
                        folded in _KNOWN_UPSCALER_VALUES
                        and folded not in advertised
                    ):
                        advertised.append(folded)
            if advertised:
                choices[identity] = tuple(advertised)
        recent_comments.clear()
    return values, choices


def inspect_optiscaler_ini(text: str) -> OptiScalerIniCapabilities:
    values, advertised = _ini_entries(text)
    fsr = FSR_SECTION.casefold()
    upscalers = UPSCALERS_SECTION.casefold()

    def has(section: str, key: str) -> bool:
        return (section, key.casefold()) in values

    def backend_values(key: str) -> tuple[str, ...]:
        identity = (upscalers, key.casefold())
        choices = list(advertised.get(identity, ()))
        current = values.get(identity, "").strip().casefold()
        choices = [value for value in choices if value != "auto"]
        choices.insert(0, "auto")
        if current and current not in choices:
            choices.append(current)
        # Never surface arbitrary comment words as configuration values.
        return tuple(
            value for value in choices if value in _KNOWN_UPSCALER_VALUES
        )

    force_style = "none"
    if has(fsr, "Fsr4ForceModel"):
        force_style = "model"
    elif has(fsr, "Fsr4ForceEnableInt8"):
        force_style = "boolean"
    return OptiScalerIniCapabilities(
        fsr4_update=has(fsr, "Fsr4Update"),
        force_int8_style=force_style,
        agility_sdk_upgrade=has(fsr, "FsrAgilitySDKUpgrade"),
        watermark=has(fsr, "Fsr4EnableWatermark"),
        dx11_values=backend_values("Dx11Upscaler") if has(upscalers, "Dx11Upscaler") else (),
        dx12_values=backend_values("Dx12Upscaler") if has(upscalers, "Dx12Upscaler") else (),
        vulkan_values=backend_values("VulkanUpscaler") if has(upscalers, "VulkanUpscaler") else (),
    )


def inspect_optiscaler_ini_state(text: str) -> OptiScalerIniState:
    """Read effective managed values without treating the saved profile as truth."""

    values, _advertised = _ini_entries(text)
    fsr = FSR_SECTION.casefold()
    upscalers = UPSCALERS_SECTION.casefold()

    def value(section: str, key: str) -> str:
        return values.get((section, key.casefold()), "").strip().casefold()

    update = value(fsr, "Fsr4Update")
    force_boolean = value(fsr, "Fsr4ForceEnableInt8")
    force_model = value(fsr, "Fsr4ForceModel")
    if update == "false":
        mode = "disabled"
    elif force_model == "2" or force_boolean == "true":
        mode = "force_int8"
    elif force_model == "1" or (
        update == "true" and force_boolean in {"false", ""}
    ):
        mode = "normal"
    elif update == "auto" or force_model in {"0", "auto"} or force_boolean == "auto":
        mode = "automatic"
    else:
        mode = "unknown"

    def tri_state(key: str) -> str:
        current = value(fsr, key)
        return current if current in {"true", "false", "auto"} else "unknown"

    return OptiScalerIniState(
        fsr4_mode=mode,
        watermark=tri_state("Fsr4EnableWatermark"),
        agility_sdk_upgrade=tri_state("FsrAgilitySDKUpgrade"),
        dx11_upscaler=value(upscalers, "Dx11Upscaler"),
        dx12_upscaler=value(upscalers, "Dx12Upscaler"),
        vulkan_upscaler=value(upscalers, "VulkanUpscaler"),
    )


def managed_ini_differences(
    text: str, managed_settings: Mapping[str, Any]
) -> tuple[dict[str, str], ...]:
    """Compare only GOL-owned INI keys with their last applied values.

    Whole-file hashes intentionally are not used for this comparison because
    OptiScaler and users may persist unrelated settings in the same INI.
    """

    values, _advertised = _ini_entries(text)
    sections = {
        **{key.casefold(): FSR_SECTION.casefold() for key in MANAGED_FSR_KEYS},
        **{
            key.casefold(): UPSCALERS_SECTION.casefold()
            for key in MANAGED_UPSCALER_KEYS
        },
    }
    differences: list[dict[str, str]] = []
    for raw_key, raw_expected in managed_settings.items():
        key = str(raw_key).strip()
        section = sections.get(key.casefold())
        if section is None:
            continue
        if isinstance(raw_expected, bool):
            expected = "true" if raw_expected else "false"
        else:
            expected = str(raw_expected).strip().casefold()
        actual = values.get((section, key.casefold()), "").strip().casefold()
        # OptiScaler v0.9.4 saves default-false CustomOptional values as
        # ``auto``.  In particular its own Watermark ON -> OFF sequence writes
        # auto, which avoids exporting MLSR-WATERMARK at all.  Treat that
        # upstream representation as equivalent to an older GOL-managed false.
        equivalent_auto_false = bool(
            key.casefold() in AUTO_FALSE_FSR_KEYS
            and expected == "false"
            and actual == "auto"
        )
        if actual != expected and not equivalent_auto_false:
            differences.append(
                {"key": key, "expected": expected, "actual": actual or "missing"}
            )
    return tuple(differences)


def update_ini_text(
    text: str,
    updates: Mapping[tuple[str, str], str],
) -> str:
    """Update only named section/key pairs and remove their duplicates."""

    normalized = {
        (str(section).strip().casefold(), str(key).strip().casefold()):
        (str(section).strip(), str(key).strip(), str(value))
        for (section, key), value in updates.items()
    }
    if not normalized:
        return text
    newline = "\r\n" if "\r\n" in text else "\n"
    trailing_newline = text.endswith(("\n", "\r"))
    lines = text.splitlines()
    output: list[str] = []
    seen: set[tuple[str, str]] = set()
    current_section = ""

    def append_missing(section: str) -> None:
        for identity, (_section_name, key_name, value) in normalized.items():
            if identity[0] == section and identity not in seen:
                output.append(f"{key_name}={value}")
                seen.add(identity)

    for line in lines:
        section_match = _SECTION_RE.match(line)
        if section_match:
            append_missing(current_section)
            current_section = section_match.group(1).strip().casefold()
            output.append(line)
            continue
        setting_match = _SETTING_RE.match(line)
        if setting_match:
            identity = (current_section, setting_match.group(2).strip().casefold())
            replacement = normalized.get(identity)
            if replacement is not None:
                if identity not in seen:
                    output.append(
                        f"{setting_match.group(1)}{replacement[1]}"
                        f"{setting_match.group(3)}{replacement[2]}"
                    )
                    seen.add(identity)
                continue
        output.append(line)
    append_missing(current_section)
    for identity, (section_name, key_name, value) in normalized.items():
        if identity in seen:
            continue
        if output and output[-1].strip():
            output.append("")
        output.extend((f"[{section_name}]", f"{key_name}={value}"))
        seen.add(identity)
    rendered = newline.join(output)
    if trailing_newline or rendered:
        rendered += newline
    return rendered


def managed_ini_updates(
    *,
    fsr4_mode: str,
    agility_sdk_upgrade: bool,
    watermark: bool,
    dx11_upscaler: str,
    dx12_upscaler: str,
    vulkan_upscaler: str,
    capabilities: OptiScalerIniCapabilities,
) -> dict[tuple[str, str], str]:
    """Translate profile choices to keys supported by this installed INI."""

    mode = str(fsr4_mode).casefold()
    if mode not in {"automatic", "normal", "force_int8", "disabled"}:
        raise ValueError("unsupported FSR4 mode")
    if not capabilities.supports_fsr4:
        raise ValueError("the installed OptiScaler release does not expose FSR4 controls")
    if mode == "force_int8" and not capabilities.supports_force_int8:
        raise ValueError("the installed OptiScaler release does not expose Force INT8")
    if mode == "disabled" and not capabilities.fsr4_update:
        raise ValueError(
            "the installed OptiScaler release cannot explicitly disable FSR4"
        )
    updates: dict[tuple[str, str], str] = {}
    if capabilities.fsr4_update:
        updates[(FSR_SECTION, "Fsr4Update")] = {
            "automatic": "auto",
            "normal": "true",
            "force_int8": "true",
            "disabled": "false",
        }[mode]
    if capabilities.force_int8_style == "model":
        updates[(FSR_SECTION, "Fsr4ForceModel")] = (
            "2" if mode == "force_int8" else "0" if mode in {"normal", "disabled"} else "auto"
        )
    elif capabilities.force_int8_style == "boolean":
        updates[(FSR_SECTION, "Fsr4ForceEnableInt8")] = (
            "true" if mode == "force_int8" else "false" if mode in {"normal", "disabled"} else "auto"
        )
    if capabilities.agility_sdk_upgrade:
        updates[(FSR_SECTION, "FsrAgilitySDKUpgrade")] = (
            "true" if agility_sdk_upgrade else "auto"
        )
    if capabilities.watermark:
        updates[(FSR_SECTION, "Fsr4EnableWatermark")] = (
            "true" if watermark else "auto"
        )
    for key, selected, available in (
        ("Dx11Upscaler", dx11_upscaler, capabilities.dx11_values),
        ("Dx12Upscaler", dx12_upscaler, capabilities.dx12_values),
        ("VulkanUpscaler", vulkan_upscaler, capabilities.vulkan_values),
    ):
        if not available:
            continue
        value = str(selected).casefold()
        if value not in available:
            raise ValueError(f"{key}={value} is not advertised by the installed OptiScaler INI")
        updates[(UPSCALERS_SECTION, key)] = value
    return updates


def recommend_fsr4(
    gpu_name: str,
    graphics_api: str,
    capabilities: OptiScalerIniCapabilities,
) -> Fsr4Recommendation:
    """Return an explainable, deliberately conservative FSR4 recommendation."""

    if not capabilities.supports_fsr4:
        return Fsr4Recommendation(
            "unsupported", "automatic", "Unsupported by installed OptiScaler",
            "The installed OptiScaler INI does not expose FSR4 update support.",
        )
    api = str(graphics_api or "").strip().casefold()
    dx12 = any(token in api for token in ("direct3d 12", "directx 12", "dx12", "d3d12"))
    dx11 = any(token in api for token in ("direct3d 11", "directx 11", "dx11", "d3d11"))
    vulkan = "vulkan" in api
    api_unknown = not api or api == "unknown"
    interop = (
        dx11 and any(value.endswith("_12") for value in capabilities.dx11_values)
    ) or (
        vulkan and any(value.endswith("_12") for value in capabilities.vulkan_values)
    )
    if not dx12 and not interop and not api_unknown:
        return Fsr4Recommendation(
            "unsupported", "automatic", "Unsupported graphics path",
            "The detected graphics API has no advertised OptiScaler DX12 or DX12-interop FSR path.",
            capabilities.supports_force_int8,
        )

    gpu = " ".join(str(gpu_name or "").casefold().split())
    force_available = capabilities.supports_force_int8
    if re.search(r"(?:radeon\s+)?rx\s*9\d{3}\b", gpu) or "rdna4" in gpu:
        if api_unknown:
            return Fsr4Recommendation(
                "unknown", "automatic", "Compatibility needs graphics API detection",
                "The GPU matches the native FSR4 generation, but the active graphics path is unknown.",
                force_available,
            )
        return Fsr4Recommendation(
            "native", "normal", "FSR 4.1.1",
            "The GPU matches the native FP8 FSR4 generation and the game exposes a compatible graphics path.",
            force_available,
        )
    if re.search(r"(?:radeon\s+)?rx\s*7\d{3}\b", gpu) or "rdna3" in gpu:
        if api_unknown:
            return Fsr4Recommendation(
                "unknown", "automatic", "Compatibility needs graphics API detection",
                "The GPU matches an upstream-supported desktop INT8 generation, but the active graphics path is unknown.",
                force_available,
            )
        return Fsr4Recommendation(
            "normal", "normal", "FSR 4.1.1 (official INT8 path)",
            "Current OptiScaler can select the official SDK INT8 model for this desktop GPU without forcing it.",
            force_available,
        )
    if re.search(r"(?:radeon\s+)?rx\s*6\d{3}\b", gpu) or "rdna2" in gpu:
        if force_available and not api_unknown:
            return Fsr4Recommendation(
                "force_int8", "force_int8", "FSR 4.1.1 INT8 - Experimental",
                "This GPU lacks the native FP8 path; OptiScaler's forced INT8 mode may work but requires in-game verification.",
                True,
            )
        return Fsr4Recommendation(
            "unknown", "automatic", "Experimental compatibility unknown",
            "No automatic override was selected because Force INT8 or the active graphics path could not be confirmed.",
            force_available,
        )
    if (
        re.search(r"(?:radeon\s+)?rx\s*(?:[45]\d{2}|5\d{3})\b", gpu)
        or re.search(r"\bradeon\s+(?:r[579]|hd)\b", gpu)
        or any(marker in gpu for marker in ("polaris", "vega", "radeon vii"))
    ):
        return Fsr4Recommendation(
            "unsupported", "automatic", "Unsupported by known GPU requirements",
            "This pre-RDNA2 GPU is not reported as FSR4-capable.",
            False,
        )
    if re.search(r"\bgtx\s*\d+", gpu):
        return Fsr4Recommendation(
            "unsupported", "automatic", "Unsupported by known GPU requirements",
            "This GPU does not expose the modern feature set required by the experimental FSR4 path.",
            False,
        )
    if re.search(r"\brtx\s*(?:20|30|40|50)\d{2}\b", gpu) or re.search(
        r"\bintel\s+arc\s+[ab]\d+", gpu
    ):
        return Fsr4Recommendation(
            "unknown", "automatic", "Force INT8 available - Experimental" if force_available else "Compatibility unknown",
            "OptiScaler exposes an experimental INT8 override, but this GPU is not an upstream-confirmed automatic target.",
            force_available,
        )
    return Fsr4Recommendation(
        "unknown", "automatic", "Compatibility unknown",
        "The GPU architecture could not be established; no automatic FSR4 override was applied.",
        force_available,
    )


__all__ = [
    "Fsr4Recommendation",
    "MANAGED_FSR_KEYS",
    "MANAGED_UPSCALER_KEYS",
    "OptiScalerIniCapabilities",
    "inspect_optiscaler_ini",
    "managed_ini_updates",
    "recommend_fsr4",
    "update_ini_text",
]
