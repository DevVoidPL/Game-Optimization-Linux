"""Registry of measurable, reversible optimization candidates.

Each candidate wraps a setting whose semantics already exist in GameOpti
(optimization profile, Proton Tweaks, GPU-memory profile). A candidate
declares exactly what it changes, when it applies, what it conflicts with,
which metrics it is expected to move and how it is restored. Nothing here
writes files; the controller applies the returned changes with a restore
point. Frame generation, ReShade/vkBasalt, OptiScaler/DLSS Enabler, modified
FSR DLLs and system packages are never candidates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .metrics import SessionMetrics

GOALS = ("maximum_performance", "balanced", "low_power", "lowest_latency", "stable_image", "best_quality")
FPS_STEPS = (30, 40, 45, 48, 50, 60, 72, 75, 90, 100, 120, 144, 165, 180, 240)

# Launcher keys produced by capabilities.launcher_kind().
RUNNER_LAUNCHERS = ("steam_native", "local_native")
HEROIC_LAUNCHERS = ("heroic_native", "heroic_flatpak")
MANUAL_LAUNCHERS = ("manual_native", "manual_wine", "manual_proton")
# Exact reasons for a launcher a candidate does not support.
LAUNCHER_REASONS = {
    ("vkd3d_no_upload_hvv", "manual"): "GameOpti applies no_upload_hvv only through the Steam runner, where the Proton/vkd3d of the launch is verified",
    ("vkd3d_no_upload_hvv", "heroic"): "The Proton/vkd3d used by the launcher is not established, so no_upload_hvv is not applied",
    ("gpu_memory_priority", "heroic"): "The launcher Flatpak cannot reach the host systemd scope",
    ("proton_no_ntsync", "manual"): "PROTON_NO_NTSYNC is read only by a Proton runner",
    ("proton_no_ntsync", "local"): "The local native game does not run through Proton",
}


@dataclass(frozen=True, slots=True)
class Change:
    target: str  # "optimization" | "proton_tweaks" | "gpu_memory"
    setting: str
    before: Any
    after: Any
    label: str

    def to_dict(self) -> dict[str, Any]:
        return {"target": self.target, "setting": self.setting, "before": self.before,
                "after": self.after, "label": self.label}


@dataclass(frozen=True, slots=True)
class CandidateContext:
    launcher: str
    profile: Any  # GameOptimizationProfile
    goal: str
    tweak_states: Mapping[str, str] = field(default_factory=dict)
    proton: bool = False
    proton_support: Mapping[str, str] = field(default_factory=dict)
    gamemode_available: bool = False
    gamescope_available: bool = False
    gamescope_filters: tuple[str, ...] = ()
    gpu_memory: Mapping[str, Any] = field(default_factory=dict)
    api: str = "Unknown"
    api_confidence: float = 0.0
    frame_generation_active: bool = False
    display: Mapping[str, Any] = field(default_factory=dict)
    baseline: SessionMetrics | None = None
    fps_floor: int = 0
    quality_consent: bool = False
    explicit_experiments: bool = False
    category: str = "unknown"
    launcher_integration_active: bool = True
    # User-owned launch configuration (Manual entry and custom launch profile).
    user_environment: frozenset[str] = frozenset()
    user_programs: tuple[str, ...] = ()

    @property
    def gamescope_active(self) -> bool:
        return bool(self.profile.gamescope_enabled and self.profile.gamescope_mode != "disabled")


@dataclass(frozen=True, slots=True)
class Applicability:
    available: bool
    code: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"available": self.available, "code": self.code, "reason": self.reason}


def _yes() -> Applicability:
    return Applicability(True)


def _no(code: str, reason: str) -> Applicability:
    return Applicability(False, code, reason)


@dataclass(frozen=True, slots=True)
class CandidateDefinition:
    id: str
    title: str
    mechanism: str
    settings: tuple[str, ...]
    prerequisites: tuple[str, ...]
    conflicts: tuple[str, ...]
    launchers: tuple[str, ...]
    goals: tuple[str, ...]
    expected: Mapping[str, str]
    image_quality: str  # "none" | "presentation" | "lossy_upscaling"
    rendered_fps: str  # "may_change" | "limits" | "unchanged_expected"
    generated_frames: bool
    restore: str
    evidence_source: str
    explicit_only: bool = False
    quality_consent: bool = False
    intended_variables: tuple[str, ...] = ()
    check: Callable[[CandidateContext], Applicability] = field(default=lambda _ctx: _yes(), compare=False, repr=False)
    plan: Callable[[CandidateContext], tuple[Change, ...]] = field(default=lambda _ctx: (), compare=False, repr=False)

    def declaration(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "mechanism": self.mechanism,
                "settings": list(self.settings), "prerequisites": list(self.prerequisites),
                "conflicts": list(self.conflicts), "launchers": list(self.launchers),
                "goals": list(self.goals), "expected": dict(self.expected),
                "imageQuality": self.image_quality, "renderedFps": self.rendered_fps,
                "generatedFrames": self.generated_frames, "restore": self.restore,
                "evidenceSource": self.evidence_source, "explicitOnly": self.explicit_only,
                "qualityConsent": self.quality_consent, "intendedVariables": list(self.intended_variables)}


def _user_conflict(ctx: CandidateContext, keys: tuple[str, ...] = (), programs: tuple[str, ...] = ()) -> Applicability | None:
    for key in keys:
        if key in ctx.user_environment:
            return _no("custom_launch_conflict", f"{key} is set in your launch configuration; GameOpti does not override it")
    for program in programs:
        if program in ctx.user_programs:
            return _no("custom_launch_conflict", f"{program} is already part of your launch command")
    return None


def _gamemode_check(ctx: CandidateContext) -> Applicability:
    conflict = _user_conflict(ctx, programs=("gamemoderun",))
    if conflict:
        return conflict
    if not ctx.gamemode_available:
        return _no("tool_unavailable", "GameMode is not available on this system")
    if ctx.profile.manual_overrides.get("gamemode"):
        return _no("manual_override", "GameMode is an explicit per-game choice")
    return _yes()


def _gamemode_plan(ctx: CandidateContext) -> tuple[Change, ...]:
    current = bool(ctx.profile.gamemode_enabled)
    return (Change("optimization", "gamemode_enabled", current, not current, "GameMode"),)


def _display_size(ctx: CandidateContext) -> tuple[int, int, int] | None:
    width, height = ctx.display.get("width"), ctx.display.get("height")
    refresh = ctx.display.get("refresh")
    if not isinstance(width, int) or not isinstance(height, int) or width < 320 or height < 320:
        return None
    return width, height, int(round(refresh)) if isinstance(refresh, (int, float)) and refresh >= 15 else 60


def _gamescope_check(ctx: CandidateContext) -> Applicability:
    conflict = _user_conflict(ctx, ("ENABLE_GAMESCOPE_WSI", "GAMESCOPE_WAYLAND_DISPLAY"), ("gamescope",))
    if conflict:
        return conflict
    if not ctx.gamescope_available:
        return _no("tool_unavailable", "Gamescope is not available on this system")
    if ctx.profile.manual_overrides.get("gamescope"):
        return _no("manual_override", "Gamescope is an explicit per-game choice")
    if ctx.frame_generation_active:
        return _no("frame_generation_active", "Frame generation is active; its Gamescope interaction is a separate experiment")
    if not ctx.gamescope_active and _display_size(ctx) is None:
        return _no("display_unknown", "The display resolution is unknown, so a native Gamescope size cannot be set")
    return _yes()


def _gamescope_plan(ctx: CandidateContext) -> tuple[Change, ...]:
    if ctx.gamescope_active:
        return (Change("optimization", "gamescope_enabled", True, False, "Gamescope"),
                Change("optimization", "gamescope_mode", ctx.profile.gamescope_mode, "disabled", "Gamescope mode"))
    width, height, refresh = _display_size(ctx) or (1920, 1080, 60)
    profile = ctx.profile
    # Without Gamescope the FPS target is advisory; with it, the target would
    # become a -r limit. "unlimited" keeps the cap out of this experiment.
    cap = (() if profile.target_fps_mode == "unlimited" else
           (Change("optimization", "target_fps_mode", profile.target_fps_mode, "unlimited", "FPS limit mode"),))
    return (
        *cap,
        Change("optimization", "gamescope_enabled", False, True, "Gamescope"),
        Change("optimization", "gamescope_mode", profile.gamescope_mode, "native", "Gamescope mode"),
        Change("optimization", "gamescope_input_auto", profile.gamescope_input_auto, False, "Render size"),
        Change("optimization", "gamescope_output_auto", profile.gamescope_output_auto, False, "Output size"),
        Change("optimization", "gamescope_input_width", profile.gamescope_input_width, width, "Render width"),
        Change("optimization", "gamescope_input_height", profile.gamescope_input_height, height, "Render height"),
        Change("optimization", "gamescope_output_width", profile.gamescope_output_width, width, "Output width"),
        Change("optimization", "gamescope_output_height", profile.gamescope_output_height, height, "Output height"),
        Change("optimization", "gamescope_refresh_rate", profile.gamescope_refresh_rate, refresh, "Refresh rate"),
        Change("optimization", "gamescope_filter", profile.gamescope_filter, "linear", "Filter"),
    )


def _fps_cap_value(ctx: CandidateContext) -> int | None:
    metrics = ctx.baseline
    average = metrics.value("averageFps") if metrics else None
    if average is None:
        return None
    if ctx.goal == "low_power" and ctx.fps_floor > 0:
        return next((step for step in FPS_STEPS if step >= ctx.fps_floor), None)
    headroom = average * 0.9
    choices = [step for step in FPS_STEPS if step <= headroom]
    return choices[-1] if choices else None


def _current_cap(ctx: CandidateContext) -> int:
    profile = ctx.profile
    if profile.target_fps_mode == "unlimited":
        return 0
    return int(profile.target_fps)


def _fps_cap_check(ctx: CandidateContext) -> Applicability:
    if not ctx.gamescope_active:
        return _no("requires_gamescope", "A measured cap uses the active Gamescope limiter; GameOpti recordings replace MangoHud's own limiter")
    if ctx.profile.gamescope_nested_refresh > 0 or ctx.profile.gamescope_framerate_limit > 0:
        return _no("virtual_refresh_set", "An explicit Gamescope refresh or frame limit is set; it would override the cap")
    if ctx.baseline is None or ctx.baseline.value("averageFps") is None:
        return _no("baseline_required", "The cap is derived from the measured baseline")
    cap = _fps_cap_value(ctx)
    current = _current_cap(ctx)
    if cap is None:
        return _no("no_cap_value", "No standard cap fits the measured frame rate and selected floor")
    if current and cap >= current:
        return _no("already_capped", f"The game is already limited to {current} FPS")
    return _yes()


def _fps_cap_plan(ctx: CandidateContext) -> tuple[Change, ...]:
    cap = _fps_cap_value(ctx) or 60
    return (Change("optimization", "target_fps_mode", ctx.profile.target_fps_mode, "manual", "FPS limit mode"),
            Change("optimization", "target_fps", ctx.profile.target_fps, cap, "FPS limit"))


def _upscale_check(ctx: CandidateContext) -> Applicability:
    if not ctx.gamescope_active:
        return _no("requires_gamescope", "Gamescope must already be active in the baseline")
    if "fsr" not in ctx.gamescope_filters:
        return _no("fsr_filter_unavailable", "The installed Gamescope does not list the FSR filter")
    if ctx.profile.gamescope_input_auto or ctx.profile.gamescope_output_auto:
        return _no("automatic_size", "Gamescope chooses the render size automatically; set an explicit size first")
    if ctx.profile.gamescope_filter == "fsr" or ctx.profile.gamescope_input_width < ctx.profile.gamescope_output_width:
        return _no("already_upscaling", "Gamescope already renders below the output size")
    if not ctx.quality_consent:
        return _no("quality_consent_required", "Upscaling lowers the render resolution; allow image-quality changes first")
    return _yes()


def _upscale_plan(ctx: CandidateContext) -> tuple[Change, ...]:
    profile = ctx.profile
    width = max(320, int(round(profile.gamescope_output_width / 1.3 / 2)) * 2)
    height = max(320, int(round(profile.gamescope_output_height / 1.3 / 2)) * 2)
    return (Change("optimization", "gamescope_input_width", profile.gamescope_input_width, width, "Render width"),
            Change("optimization", "gamescope_input_height", profile.gamescope_input_height, height, "Render height"),
            Change("optimization", "gamescope_filter", profile.gamescope_filter, "fsr", "Filter"))


def _ntsync_check(ctx: CandidateContext) -> Applicability:
    conflict = _user_conflict(ctx, ("PROTON_NO_NTSYNC",))
    if conflict:
        return conflict
    if not ctx.proton:
        return _no("not_proton", "The game does not run through a detected Proton runner")
    if ctx.proton_support.get("no_ntsync") != "present":
        return _no("runner_lacks_option", "The selected Proton runner does not implement PROTON_NO_NTSYNC")
    if ctx.tweak_states.get("no_ntsync", "default") != "default":
        return _no("manual_override", "ntsync already has an explicit Proton Tweaks setting")
    return _yes()


def _ntsync_plan(ctx: CandidateContext) -> tuple[Change, ...]:
    return (Change("proton_tweaks", "no_ntsync", "default", "enabled", "Disable ntsync"),)


def _gpu_priority_check(ctx: CandidateContext) -> Applicability:
    status = ctx.gpu_memory
    if not status.get("ready") or not status.get("region"):
        return _no("gpu_memory_not_ready", "GPU-memory protection is not prepared or no memory region is selected")
    if status.get("priority"):
        return _no("already_active", "GPU-memory priority is already enabled")
    if ctx.gamescope_active:
        return _no("gamescope_conflict", "Gamescope rewrites dmem.low on focus changes, so protection cannot be isolated")
    if status.get("baselineProtection") != "unprotected":
        return _no("external_protection", "The baseline did not prove that the game ran without other memory protection")
    return _yes()


def _gpu_priority_plan(ctx: CandidateContext) -> tuple[Change, ...]:
    return (Change("gpu_memory", "priority", False, True, "GPU-memory priority"),)


def _upload_check(ctx: CandidateContext) -> Applicability:
    conflict = _user_conflict(ctx, ("VKD3D_CONFIG",))
    if conflict:
        return conflict
    status = ctx.gpu_memory
    if ctx.api != "Direct3D 12" or ctx.api_confidence < 0.8:
        return _no("api_not_d3d12", "Direct3D 12 is not established for this game")
    if not status.get("pressureSupported"):
        return _no("vkd3d_unsupported", "The selected Proton/vkd3d does not offer no_upload_hvv or ReBAR is not verified")
    if status.get("pressure", "default") != "default":
        return _no("already_active", "The UPLOAD system-memory profile is already enabled")
    return _yes()


def _upload_plan(ctx: CandidateContext) -> tuple[Change, ...]:
    return (Change("gpu_memory", "pressure", "default", "upload-system-memory", "vkd3d no_upload_hvv"),)


REGISTRY: tuple[CandidateDefinition, ...] = (
    CandidateDefinition(
        "gamemode", "GameMode", "Toggle the GameMode wrapper for this game",
        ("optimization.json: gamemode_enabled",), ("GameMode installed and its service reachable",),
        ("An explicit per-game GameMode choice",), (*RUNNER_LAUNCHERS, *HEROIC_LAUNCHERS, *MANUAL_LAUNCHERS),
        ("maximum_performance", "balanced", "lowest_latency", "best_quality"),
        {"averageFps": "up_or_unchanged", "p99FrametimeMs": "down_or_unchanged", "cpuClockMhz": "up_or_unchanged"},
        "none", "may_change", False, "Restore the previous optimization profile byte for byte",
        "GameMode documentation: CPU governor, I/O and scheduling requests while the game runs",
        check=_gamemode_check, plan=_gamemode_plan),
    CandidateDefinition(
        "gamescope_native", "Gamescope (native size)", "Toggle Gamescope at the display resolution without scaling",
        ("optimization.json: gamescope_*",), ("Gamescope installed", "Known display resolution"),
        ("Frame generation", "An explicit per-game Gamescope choice", "GPU-memory priority (dmem.low)"),
        (*RUNNER_LAUNCHERS, *HEROIC_LAUNCHERS, *MANUAL_LAUNCHERS), ("balanced", "stable_image", "lowest_latency"),
        {"p99FrametimeMs": "down_or_unchanged", "averageFps": "unchanged_or_down"},
        "presentation", "may_change", False, "Restore the previous optimization profile byte for byte",
        "Existing GameOpti Gamescope launch plan (native mode = render size equals output size)",
        check=_gamescope_check, plan=_gamescope_plan),
    CandidateDefinition(
        "fps_cap", "Measured FPS limit", "Limit FPS through the active Gamescope limiter at a value derived from the baseline",
        ("optimization.json: target_fps_mode, target_fps",), ("Gamescope active in the baseline", "Representative baseline"),
        ("Explicit Gamescope refresh or frame limit",), (*RUNNER_LAUNCHERS, *HEROIC_LAUNCHERS, *MANUAL_LAUNCHERS),
        ("low_power", "balanced", "stable_image"),
        {"gpuPowerW": "down", "p99FrametimeMs": "down", "averageFps": "down_to_limit"},
        "none", "limits", False, "Restore the previous optimization profile byte for byte",
        "Existing Gamescope -r limiter owned by the GameOpti launch plan",
        check=_fps_cap_check, plan=_fps_cap_plan),
    CandidateDefinition(
        "proton_no_ntsync", "Disable ntsync (Proton)", "Set PROTON_NO_NTSYNC=1 so the runner falls back to its other synchronization method",
        ("proton-tweaks.json: no_ntsync",), ("Proton runner that implements PROTON_NO_NTSYNC",),
        ("An explicit ntsync setting",), ("steam_native", *HEROIC_LAUNCHERS, "manual_proton"), ("balanced", "stable_image"),
        {"p99FrametimeMs": "unknown", "averageFps": "unchanged_or_down"},
        "none", "may_change", False, "Restore the previous Proton Tweaks profile byte for byte",
        "Proton documentation for PROTON_NO_NTSYNC (compatibility override, not a performance option)",
        explicit_only=True, check=_ntsync_check, plan=_ntsync_plan),
    CandidateDefinition(
        "gpu_memory_priority", "GPU-memory priority", "Run the game in a private systemd scope with dmem.low protection",
        ("gpu-memory.json: priority",), ("Prepared host dmem delegation", "Baseline without other memory protection"),
        ("Gamescope", "Desktop foreground boosters", "Launcher Flatpak sandbox"), ("steam_native", "local_native", *MANUAL_LAUNCHERS),
        ("maximum_performance", "best_quality"),
        {"p99FrametimeMs": "down_or_unchanged", "stuttersPerMinute": "down_or_unchanged"},
        "none", "unchanged_expected", False, "Restore the previous GPU-memory profile byte for byte",
        "GameOpti GPU-memory host bridge; live protection must be observed during the session",
        explicit_only=True, check=_gpu_priority_check, plan=_gpu_priority_plan),
    CandidateDefinition(
        "vkd3d_no_upload_hvv", "vkd3d no_upload_hvv", "Keep the UPLOAD heap in system memory (VKD3D_CONFIG=no_upload_hvv)",
        ("gpu-memory.json: pressure",), ("Direct3D 12 through vkd3d-proton with no_upload_hvv", "Verified discrete ReBAR GPU"),
        ("Other VKD3D_CONFIG owners",), ("steam_native",), ("maximum_performance", "balanced"),
        {"vramMb": "down", "averageFps": "unknown"},
        "none", "may_change", False, "Restore the previous GPU-memory profile byte for byte",
        "vkd3d-proton documentation for no_upload_hvv; verified by the existing GPU-memory checks",
        explicit_only=True, check=_upload_check, plan=_upload_plan),
    CandidateDefinition(
        "gamescope_fsr_upscale", "Gamescope FSR upscaling", "Render at about 77% of the output size and upscale with Gamescope FSR",
        ("optimization.json: gamescope_input_width, gamescope_input_height, gamescope_filter",),
        ("Gamescope active with an explicit size", "Gamescope FSR filter", "Image-quality consent"),
        ("Existing upscaling",), (*RUNNER_LAUNCHERS, *HEROIC_LAUNCHERS, *MANUAL_LAUNCHERS), ("maximum_performance", "low_power"),
        {"averageFps": "up", "gpuPowerW": "down"},
        "lossy_upscaling", "may_change", False, "Restore the previous optimization profile byte for byte",
        "Gamescope FSR 1 filter documentation; image quality is reduced by design",
        explicit_only=True, quality_consent=True, intended_variables=("renderResolution", "upscaler"),
        check=_upscale_check, plan=_upscale_plan),
)
BY_ID = {item.id: item for item in REGISTRY}

# Ordering hint only: a category never makes a candidate available.
_CATEGORY_HINT = {"competitive": ("gamemode",), "fast_action": ("gamemode",),
                  "strategy_simulation": ("gamemode",), "cinematic": ("fps_cap", "gamescope_native")}


def evaluate(ctx: CandidateContext, tested: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """Every candidate with its applicability and exact changes, best first."""

    tested = dict(tested or {})
    hints = _CATEGORY_HINT.get(ctx.category, ())
    rows: list[tuple[tuple[int, int, int, int], dict[str, Any]]] = []
    for index, item in enumerate(REGISTRY):
        if ctx.launcher not in item.launchers:
            family = ctx.launcher.split("_", 1)[0]
            state = _no("launcher_unsupported", LAUNCHER_REASONS.get((item.id, family), "Not verified for this launcher"))
        elif not ctx.launcher_integration_active and ctx.launcher in HEROIC_LAUNCHERS:
            state = _no("integration_not_applied", "Apply the GameOpti launcher integration first so the change reaches the launcher")
        elif item.explicit_only and not ctx.explicit_experiments:
            state = _no("explicit_only", "Shown only when explicit experiments are allowed")
        else:
            state = item.check(ctx)
        if state.available and item.id in tested:
            state = _no("tested_in_scope", f"Already measured for this game and system: {tested[item.id]}")
        changes = item.plan(ctx) if state.available else ()
        relevant = ctx.goal in item.goals
        rank = (0 if state.available else 1, 0 if relevant else 1, 0 if item.id in hints else 1, index)
        rows.append((rank, {**item.declaration(), "applicability": state.to_dict(), "relevantForGoal": relevant,
                            "categoryHint": item.id in hints, "changes": [change.to_dict() for change in changes]}))
    rows.sort(key=lambda row: row[0])
    return [row for _rank, row in rows]


def proposal(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """One candidate: the first available that is relevant for the goal."""

    return next((row for row in rows if row["applicability"]["available"] and row["relevantForGoal"]), None)


def verify_activation(candidate: Mapping[str, Any], *, launcher: str, report: Mapping[str, Any] | None,
                      session_id: str, gpu_summary: Mapping[str, Any] | None = None,
                      launcher_status: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Whether the measured session actually ran with the candidate."""

    candidate_id = str(candidate.get("id") or "")
    changes = {item.get("setting"): item.get("after") for item in candidate.get("changes", ())}
    if launcher in HEROIC_LAUNCHERS:
        status = dict(launcher_status or {})
        if status.get("state") == "applied":
            return {"verified": True, "level": "configured",
                    "reason": "The launcher settings contained the change when the session started; the process itself is not inspected"}
        return {"verified": False, "level": "none", "reason": "The launcher settings did not contain the change"}
    report = dict(report or {})
    if str(report.get("baselineSessionId") or "") != session_id:
        return {"verified": False, "level": "none", "reason": "No runner report for this measurement session"}
    wrappers = list(report.get("wrappers") or [])
    sources = dict(report.get("environmentSources") or {})
    ok, reason = False, "The candidate has no activation check"
    if candidate_id == "gamemode":
        ok = ("gamemode" in wrappers) == bool(changes.get("gamemode_enabled"))
        reason = "GameMode wrapper presence matched the candidate" if ok else "GameMode wrapper presence did not match"
    elif candidate_id == "gamescope_native":
        ok = ("gamescope" in wrappers) == bool(changes.get("gamescope_enabled"))
        reason = "Gamescope wrapper presence matched the candidate" if ok else "Gamescope wrapper presence did not match"
    elif candidate_id == "fps_cap":
        ok = report.get("fpsLimitOwner") == "gamescope" and int(report.get("fpsLimit") or 0) == int(changes.get("target_fps") or -1)
        reason = "Gamescope owned the measured FPS limit" if ok else "The measured launch did not use the candidate FPS limit"
    elif candidate_id == "gamescope_fsr_upscale":
        argv = [str(item) for item in report.get("gamescopeWrapper") or []]
        ok = "fsr" in argv and str(changes.get("gamescope_input_width")) in argv
        reason = "Gamescope rendered below the output size with FSR" if ok else "Gamescope FSR arguments were absent"
    elif candidate_id == "proton_no_ntsync":
        ok = sources.get("PROTON_NO_NTSYNC") == "proton_tweaks"
        reason = "PROTON_NO_NTSYNC was set by Proton Tweaks" if ok else "PROTON_NO_NTSYNC was absent from the launch"
    elif candidate_id == "vkd3d_no_upload_hvv":
        ok = sources.get("VKD3D_CONFIG") == "gpu_memory"
        reason = "VKD3D_CONFIG came from the GPU-memory profile" if ok else "VKD3D_CONFIG no_upload_hvv was absent"
    elif candidate_id == "gpu_memory_priority":
        summary = dict(gpu_summary or {})
        samples = int(summary.get("samples") or 0)
        protected = int(summary.get("protectedSamples") or 0)
        ok = bool(summary.get("priorityRequested")) and samples > 0 and protected >= samples * 0.95
        reason = (f"Protection was observed in {protected} of {samples} samples" if samples
                  else "No live protection samples were recorded")
    return {"verified": ok, "level": "observed" if ok else "none", "reason": reason}


__all__ = [
    "BY_ID", "CandidateContext", "CandidateDefinition", "Change", "FPS_STEPS", "GOALS",
    "HEROIC_LAUNCHERS", "LAUNCHER_REASONS", "MANUAL_LAUNCHERS", "REGISTRY", "RUNNER_LAUNCHERS", "evaluate", "proposal", "verify_activation",
]
