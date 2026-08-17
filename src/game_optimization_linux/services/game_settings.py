from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
from typing import Iterable

from game_optimization_linux.models import (
    BottleneckAnalysis,
    DetectedGameSetting,
    FrameRateAnalysis,
    Game,
    GameFingerprint,
    GameSettingsAnalysis,
    OptimizationCandidate,
    PerformanceMeasurement,
)


_SECTION = re.compile(r"^\s*\[([^\]]+)\]\s*$")
_ASSIGNMENT = re.compile(
    r"^(?P<prefix>\s*(?P<key>[^#;=\s][^=]*?)\s*=\s*)"
    r"(?P<value>[^;#\r\n]*?)(?P<suffix>\s*(?:[;#].*)?)$"
)
_MAX_CONFIG_SIZE = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class SettingRule:
    id: str
    label: str
    category: str
    keys: tuple[str, ...]
    value_kind: str
    bottlenecks: tuple[str, ...]
    performance_impact: str
    quality_impact: str
    confidence: str


@dataclass(frozen=True, slots=True)
class ConfigValue:
    path: Path
    adapter: str
    section: str
    key: str
    value: str
    rule: SettingRule
    sha256: str


_UNREAL_RULES = (
    SettingRule(
        "unreal_ray_tracing", "Ray tracing", "Ray tracing",
        ("r.RayTracing",), "boolean", ("gpu_bottleneck",),
        "high", "high", "high",
    ),
    SettingRule(
        "unreal_shadow_quality", "Shadow quality", "Shadows",
        ("sg.ShadowQuality",), "scalability", ("gpu_bottleneck",),
        "medium", "medium", "high",
    ),
    SettingRule(
        "unreal_effects_quality", "Effects quality", "Effects / volumetrics",
        ("sg.EffectsQuality",), "scalability", ("gpu_bottleneck",),
        "medium", "medium", "medium",
    ),
    SettingRule(
        "unreal_post_process_quality", "Post-process quality", "Post-processing",
        ("sg.PostProcessQuality",), "scalability", ("gpu_bottleneck",),
        "medium", "medium", "medium",
    ),
    SettingRule(
        "unreal_view_distance", "View distance", "View distance",
        ("sg.ViewDistanceQuality",), "scalability", ("cpu_bottleneck",),
        "medium", "medium", "medium",
    ),
    SettingRule(
        "unreal_motion_blur", "Motion blur", "Visual preference",
        ("bMotionBlur",), "boolean", (), "unknown", "low", "medium",
    ),
)

_REDENGINE_RULES = (
    SettingRule(
        "red_motion_blur", "Motion blur", "Visual preference",
        ("MotionBlur", "AllowMotionBlur"), "boolean", (),
        "unknown", "low", "medium",
    ),
)


class ConfigAdapter:
    id = ""
    engine = ""
    rules: tuple[SettingRule, ...] = ()
    file_names: tuple[str, ...] = ()

    def discover(self, game: Game, fingerprint: GameFingerprint) -> tuple[Path, ...]:
        paths: list[Path] = []
        for raw in fingerprint.config_locations:
            directory = Path(raw)
            try:
                entries = tuple(directory.iterdir())
            except OSError:
                continue
            for path in entries:
                if path.name.casefold() in self.file_names:
                    paths.append(path)
        for root in proton_user_roots(game):
            try:
                for path in root.rglob("*"):
                    if len(paths) >= 64:
                        break
                    if path.name.casefold() in self.file_names:
                        paths.append(path)
            except OSError:
                continue
        unique: dict[str, Path] = {}
        for path in paths:
            try:
                resolved = path.resolve(strict=True)
                if (
                    resolved.is_file()
                    and not path.is_symlink()
                    and resolved.stat().st_size <= _MAX_CONFIG_SIZE
                    and is_allowed_config_path(game, resolved)
                ):
                    unique[os.fspath(resolved)] = resolved
            except OSError:
                continue
        return tuple(unique.values())

    def inspect(self, path: Path) -> tuple[ConfigValue, ...]:
        data = path.read_bytes()
        text = data.decode("utf-8-sig")
        parsed = parse_assignments(text)
        if not parsed:
            raise ValueError(f"{path.name} is not a supported text configuration")
        digest = hashlib.sha256(data).hexdigest()
        rules = {key.casefold(): rule for rule in self.rules for key in rule.keys}
        results: list[ConfigValue] = []
        for section, key, value in parsed:
            rule = rules.get(key.casefold())
            if rule is None or not available_values(rule, value):
                continue
            results.append(ConfigValue(path, self.id, section, key, value, rule, digest))
        return tuple(results)


class UnrealConfigAdapter(ConfigAdapter):
    id = "unreal_ini"
    engine = "Unreal Engine"
    rules = _UNREAL_RULES
    file_names = ("gameusersettings.ini", "engine.ini")


class RedEngineConfigAdapter(ConfigAdapter):
    id = "redengine_text"
    engine = "REDengine"
    rules = _REDENGINE_RULES
    file_names = ("user.settings",)


class GameSettingsAdvisor:
    def __init__(self, adapters: Iterable[ConfigAdapter] | None = None) -> None:
        self._adapters = tuple(adapters or (UnrealConfigAdapter(), RedEngineConfigAdapter()))

    def analyze(
        self,
        game: Game,
        fingerprint: GameFingerprint,
        measurement: PerformanceMeasurement | None,
        bottleneck: BottleneckAnalysis,
        frame_rate: FrameRateAnalysis,
    ) -> tuple[GameSettingsAnalysis, tuple[OptimizationCandidate, ...]]:
        adapter = next(
            (item for item in self._adapters if item.engine == fingerprint.engine.value),
            None,
        )
        if adapter is None:
            return (
                GameSettingsAnalysis(
                    "unsupported", fingerprint.engine.value, (), (),
                    "Automatic graphics settings optimization is not available for this game yet.",
                    "unsupported_engine",
                ),
                (),
            )
        files = adapter.discover(game, fingerprint)
        if not files:
            return (
                GameSettingsAnalysis(
                    "unavailable", fingerprint.engine.value, (), (),
                    "No supported existing configuration file was found.",
                    "missing_config",
                ),
                (),
            )
        values: list[ConfigValue] = []
        invalid: list[str] = []
        for path in files:
            try:
                values.extend(adapter.inspect(path))
            except (OSError, UnicodeError, ValueError):
                invalid.append(os.fspath(path))
        if not values:
            message = (
                "Configuration files were invalid or unsupported."
                if invalid else "No safely modifiable settings were found."
            )
            return (
                GameSettingsAnalysis(
                    "invalid" if invalid else "available",
                    fingerprint.engine.value,
                    tuple(os.fspath(path) for path in files),
                    (),
                    message,
                    "invalid_config" if invalid else "no_supported_settings",
                ),
                (),
            )

        recommendations: list[OptimizationCandidate] = []
        baseline_usable = (
            measurement is not None
            and measurement.available
            and bottleneck.conclusion != "insufficient_data"
            and bottleneck.confidence >= 0.60
        )
        capped_with_headroom = bool(
            frame_rate.state == "likely_capped"
            and measurement is not None
            and measurement.gpu_usage_percent is not None
            and measurement.gpu_usage_percent < 92
        )
        if baseline_usable and not capped_with_headroom and bottleneck.conclusion != "balanced":
            for value in values:
                if bottleneck.conclusion not in value.rule.bottlenecks:
                    continue
                proposed = proposed_value(value.rule, value.value)
                if proposed is None:
                    continue
                recommendations.append(self._candidate(
                    value,
                    proposed,
                    fingerprint.engine.value,
                    (
                        *bottleneck.evidence,
                        f"{value.key} is present in {value.path.name}",
                    ),
                    automatic=True,
                ))
        if recommendations:
            recommendation_state = "available"
            message = "Supported settings match the measured bottleneck."
        elif measurement is None or measurement.samples <= 0:
            recommendation_state = "baseline_missing"
            message = "A representative baseline is required before recommending a settings change."
        elif not measurement.available:
            recommendation_state = "baseline_unrepresentative"
            message = "The saved baseline is not representative enough for measured settings recommendations."
        elif bottleneck.conclusion == "insufficient_data" or bottleneck.confidence < 0.60:
            recommendation_state = "bottleneck_low_confidence"
            message = "Bottleneck confidence is too low for a conservative settings change."
        elif capped_with_headroom:
            recommendation_state = "capped_with_headroom"
            message = (
                "No graphics reductions are recommended because the game appears "
                "frame-limited and the hardware has available headroom."
            )
        elif bottleneck.conclusion == "balanced":
            recommendation_state = "balanced"
            message = (
                "No graphics reductions are recommended because the measured "
                "workload is balanced."
            )
        else:
            recommendation_state = "no_matching_setting"
            message = "No supported existing setting matches the measured bottleneck."
        automatic_instances = {
            (
                candidate.files_to_modify[0],
                candidate.config_section,
                candidate.config_key,
            )
            for candidate in recommendations
            if candidate.files_to_modify
        }
        detected = tuple(
            self._detected_setting(
                value,
                automatic=(
                    os.fspath(value.path), value.section, value.key
                ) in automatic_instances,
                automatic_reason=(
                    "Automatic recommends a conservative one-step test for the measured bottleneck."
                    if (
                        os.fspath(value.path), value.section, value.key
                    ) in automatic_instances
                    else message
                ),
            )
            for value in values
        )
        return (
            GameSettingsAnalysis(
                "available",
                fingerprint.engine.value,
                tuple(os.fspath(path) for path in files),
                detected,
                message,
                recommendation_state,
            ),
            tuple(recommendations),
        )

    def manual_candidate(
        self,
        settings: GameSettingsAnalysis,
        instance_id: str,
        proposed: str,
    ) -> OptimizationCandidate:
        setting = next(
            (item for item in settings.detected if item.instance_id == instance_id),
            None,
        )
        if setting is None or not setting.modifiable:
            raise ValueError("The selected setting is not safely editable")
        if proposed not in setting.alternative_values:
            raise ValueError("The selected value is not supported for this setting")
        evidence = (
            f"{setting.key} is present in {Path(setting.file).name}",
            setting.automatic_reason,
        )
        return OptimizationCandidate(
            id=f"manual_setting:{setting.instance_id}:{proposed}",
            target=setting.category,
            mechanism=f"Existing {setting.label} setting",
            source=f"{settings.engine} configuration and manual user-directed test",
            evidence=evidence,
            current_value=setting.value,
            proposed_value=proposed,
            expected_effect=(
                "Test the effect of this supported existing setting; the actual "
                "result must be measured"
            ),
            quality_impact=setting.quality_impact,
            risk="Low; one existing text setting is changed and backed up",
            reversible=True,
            requires_measurement=True,
            engine_support=settings.engine,
            api_support="Uses an existing game configuration value",
            files_to_modify=(setting.file,),
            automatically_selected=False,
            setting_id=setting.id,
            setting_label=setting.label,
            setting_category=setting.category,
            performance_impact=setting.performance_impact,
            confidence_label=setting.confidence_label,
            config_sha256=setting.config_sha256,
            config_section=setting.section,
            config_key=setting.key,
            config_adapter=setting.adapter,
        )

    @staticmethod
    def _candidate(
        value: ConfigValue,
        proposed: str,
        engine: str,
        evidence: tuple[str, ...],
        *,
        automatic: bool,
    ) -> OptimizationCandidate:
        instance = setting_instance_id(value)
        return OptimizationCandidate(
            id=f"game_setting:{instance}:{proposed}",
            target=value.rule.category,
            mechanism=f"Existing {value.rule.label} setting",
            source=f"{engine} configuration and representative MangoHud baseline",
            evidence=evidence,
            current_value=value.value,
            proposed_value=proposed,
            expected_effect=(
                "Reduce the workload associated with this existing setting; "
                "the actual effect must be measured"
            ),
            quality_impact=value.rule.quality_impact,
            risk="Low; one existing text setting is changed and backed up",
            reversible=True,
            requires_measurement=True,
            engine_support=engine,
            api_support="Uses an existing game configuration value",
            files_to_modify=(os.fspath(value.path),),
            automatically_selected=automatic,
            setting_id=value.rule.id,
            setting_label=value.rule.label,
            setting_category=value.rule.category,
            performance_impact=value.rule.performance_impact,
            confidence_label=value.rule.confidence,
            config_sha256=value.sha256,
            config_section=value.section,
            config_key=value.key,
            config_adapter=value.adapter,
        )

    @staticmethod
    def _detected_setting(
        value: ConfigValue,
        *,
        automatic: bool,
        automatic_reason: str,
    ) -> DetectedGameSetting:
        available = available_values(value.rule, value.value)
        alternatives = alternative_values(value.rule, value.value)
        return DetectedGameSetting(
            value.rule.id,
            value.rule.label,
            value.rule.category,
            os.fspath(value.path),
            value.section,
            value.key,
            value.value,
            value.adapter,
            bool(alternatives),
            setting_instance_id(value),
            available,
            alternatives,
            alternatives[0] if alternatives else "",
            value.rule.performance_impact,
            value.rule.quality_impact,
            value.rule.confidence,
            automatic,
            automatic_reason,
            value.sha256,
        )


def parse_assignments(text: str) -> tuple[tuple[str, str, str], ...]:
    section = ""
    values: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        section_match = _SECTION.match(line)
        if section_match:
            section = section_match.group(1).strip()
            continue
        match = _ASSIGNMENT.match(line)
        if match:
            values.append((section, match.group("key").strip(), match.group("value").strip()))
    return tuple(values)


def proposed_value(rule: SettingRule, current: str) -> str | None:
    value = current.strip()
    if rule.value_kind == "boolean":
        folded = value.casefold()
        return {
            "1": "0", "true": "false", "on": "off", "yes": "no",
        }.get(folded)
    if rule.value_kind == "scalability":
        try:
            level = int(value)
        except ValueError:
            return None
        return str(level - 1) if 1 <= level <= 4 else None
    return None


def available_values(rule: SettingRule, current: str) -> tuple[str, ...]:
    value = current.strip()
    if rule.value_kind == "scalability":
        try:
            level = int(value)
        except ValueError:
            return ()
        if not 0 <= level <= 4:
            return ()
        return tuple(str(item) for item in range(level, -1, -1))
    if rule.value_kind == "boolean":
        folded = value.casefold()
        disabled = {
            "1": "0", "true": "false", "on": "off", "yes": "no",
        }.get(folded)
        if disabled is not None:
            return (value, disabled)
        if folded in {"0", "false", "off", "no"}:
            return (value,)
    return ()


def alternative_values(rule: SettingRule, current: str) -> tuple[str, ...]:
    values = available_values(rule, current)
    return values[1:] if len(values) > 1 else ()


def setting_instance_id(value: ConfigValue) -> str:
    identity = "\0".join(
        (os.fspath(value.path), value.section.casefold(), value.key.casefold())
    )
    return f"{value.rule.id}:{hashlib.sha256(identity.encode()).hexdigest()[:16]}"


def replace_existing_setting(
    data: bytes,
    *,
    section: str,
    key: str,
    current: str,
    proposed: str,
) -> bytes:
    has_bom = data.startswith(b"\xef\xbb\xbf")
    text = data.decode("utf-8-sig")
    active_section = ""
    found = 0
    output: list[str] = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        ending = line[len(body):]
        section_match = _SECTION.match(body)
        if section_match:
            active_section = section_match.group(1).strip()
            output.append(line)
            continue
        match = _ASSIGNMENT.match(body)
        if (
            match
            and active_section.casefold() == section.casefold()
            and match.group("key").strip().casefold() == key.casefold()
        ):
            if match.group("value").strip() != current:
                raise RuntimeError("The config changed after analysis")
            found += 1
            output.append(
                match.group("prefix") + proposed + match.group("suffix") + ending
            )
        else:
            output.append(line)
    if found != 1:
        raise RuntimeError("The supported config setting is missing or duplicated")
    updated_text = "".join(output)
    updated = updated_text.encode("utf-8")
    if has_bom:
        updated = b"\xef\xbb\xbf" + updated
    matches = [
        value for value in parse_assignments(updated_text)
        if value[0].casefold() == section.casefold()
        and value[1].casefold() == key.casefold()
    ]
    if len(matches) != 1 or matches[0][2] != proposed:
        raise RuntimeError("The modified configuration did not validate")
    return updated


def proton_user_roots(game: Game) -> tuple[Path, ...]:
    app_id = str(game.steam_app_id or "")
    if not app_id:
        return ()
    try:
        root = game.install_path.resolve(strict=True)
    except OSError:
        return ()
    for parent in (root, *root.parents):
        if parent.name.casefold() != "steamapps":
            continue
        users = parent / "compatdata" / app_id / "pfx" / "drive_c" / "users"
        return (users,) if users.is_dir() else ()
    return ()


def allowed_config_roots(game: Game) -> tuple[Path, ...]:
    roots: list[Path] = []
    try:
        roots.append(game.install_path.resolve(strict=True))
    except OSError:
        pass
    roots.extend(path.resolve() for path in proton_user_roots(game))
    return tuple(roots)


def is_allowed_config_path(game: Game, path: Path) -> bool:
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return False
    for root in allowed_config_roots(game):
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


__all__ = [
    "ConfigAdapter",
    "GameSettingsAdvisor",
    "RedEngineConfigAdapter",
    "UnrealConfigAdapter",
    "is_allowed_config_path",
    "replace_existing_setting",
]
