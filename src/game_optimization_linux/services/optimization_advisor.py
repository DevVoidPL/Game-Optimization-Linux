"""Conservative, explainable optimization recommendations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from game_optimization_linux.models import GameOptimizationProfile
from .displays import DisplayProfile


@dataclass(frozen=True, slots=True)
class SessionPerformanceData:
    average_fps: float | None = None
    one_percent_low_fps: float | None = None
    frametime_ms: float | None = None
    gpu_usage_percent: float | None = None
    cpu_usage_percent: float | None = None
    vram_used_mb: float | None = None

    @property
    def available(self) -> bool:
        return any(value is not None for value in (
            self.average_fps, self.one_percent_low_fps, self.frametime_ms,
            self.gpu_usage_percent, self.cpu_usage_percent, self.vram_used_mb,
        ))


@dataclass(frozen=True, slots=True)
class OptimizationRecommendation:
    target_fps: int
    gamemode_recommended: bool
    gamescope_recommended: bool
    preliminary: bool
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "targetFps": self.target_fps,
            "gamemodeRecommended": self.gamemode_recommended,
            "gamescopeRecommended": self.gamescope_recommended,
            "preliminary": self.preliminary,
            "status": (
                "Preliminary recommendation - game measurement required"
                if self.preliminary else "Recommendation uses saved session measurements"
            ),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class OptimizationPresetPlan:
    profile: GameOptimizationProfile
    changes: Mapping[str, Any]
    reasons: tuple[str, ...]
    sources: tuple[str, ...]
    conflicts: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "changes": dict(self.changes),
            "reasons": list(self.reasons),
            "sources": list(self.sources),
            "conflicts": list(self.conflicts),
        }


class OptimizationAdvisor:
    def recommend(
        self,
        profile: GameOptimizationProfile,
        display: DisplayProfile | None,
        measurements: SessionPerformanceData | None = None,
        *,
        system_info: Mapping[str, Any] | None = None,
    ) -> OptimizationRecommendation:
        refresh = max(30, int(round(display.refresh_rate if display else 60)))
        category = profile.game_category
        reasons = [f"Game category: {category}"]
        if display:
            reasons.append(f"Selected display: {display.width}×{display.height} at {refresh} Hz")
        hardware = system_info or {}
        cpu = str(hardware.get("cpuModel") or hardware.get("cpu_model") or "").strip()
        gpu = str(hardware.get("gpuModel") or hardware.get("gpu_model") or "").strip()
        if cpu:
            reasons.append(f"Detected CPU: {cpu}")
        if gpu:
            reasons.append(f"Detected GPU: {gpu}")
        if category == "competitive":
            target = min(refresh, 240)
            gamemode = True
            reasons.append("Competitive profile follows the display refresh rate")
        elif category == "fast_action":
            target = min(refresh, 120)
            gamemode = True
            reasons.append("Fast action favors responsive but bounded frame rate")
        elif category in {"platformer_2d", "cinematic", "retro"}:
            target = 60
            gamemode = category == "cinematic"
            reasons.append("A stable 60 FPS is a conservative starting point for this category")
        elif category == "strategy_simulation":
            target = 60
            gamemode = False
            reasons.append("Stability is preferred over maximum frame rate")
        else:
            target = 60
            gamemode = False
            reasons.append("Unknown category keeps a non-aggressive baseline")
        if profile.user_goal == "lowest_latency":
            target = min(refresh, 240)
            gamemode = category != "unknown"
            reasons.append("User goal prioritizes low latency")
        elif profile.user_goal == "low_power":
            target = min(target, 60)
            gamemode = False
            reasons.append("User goal limits load and avoids an aggressive system profile")
        elif profile.user_goal == "best_quality":
            target = min(target, 60)
            reasons.append("User goal prioritizes image quality over maximum FPS")
        data = measurements or SessionPerformanceData()
        if data.available:
            reasons.append("Saved session measurements are available")
        else:
            reasons.extend(("No saved session measurements", "A safe preliminary profile was used"))
        return OptimizationRecommendation(
            target_fps=max(15, target), gamemode_recommended=gamemode,
            gamescope_recommended=False, preliminary=not data.available,
            reasons=tuple(reasons),
        )

    def resolve_preset(
        self,
        profile: GameOptimizationProfile,
        display: DisplayProfile | None,
        *,
        gamemode_available: bool,
        gamescope_available: bool,
        measurements: SessionPerformanceData | None = None,
        system_info: Mapping[str, Any] | None = None,
    ) -> OptimizationPresetPlan:
        preset = profile.preset
        recommendation = self.recommend(
            profile, display, measurements, system_info=system_info
        )
        if preset == "custom":
            return OptimizationPresetPlan(
                profile,
                {},
                ("Custom keeps the explicitly selected runtime options",),
                ("manual settings",),
                (),
            )

        refresh = max(30, int(round(display.refresh_rate if display else 60)))
        values: dict[str, Any] = {}
        reasons: list[str] = []
        sources = ["selected preset", "game category", "user goal"]
        conflicts: list[str] = []

        if preset == "automatic":
            values.update(
                target_fps_mode="automatic",
                target_fps=recommendation.target_fps,
                gamemode_enabled=(
                    recommendation.gamemode_recommended and gamemode_available
                ),
                gamescope_enabled=False,
                gamescope_mode="disabled",
            )
            reasons.extend(recommendation.reasons)
            reasons.append("Automatic does not enable Gamescope without an explicit user choice")
        elif preset == "maximum_performance":
            values.update(
                user_goal="lowest_latency",
                target_fps_mode="automatic",
                target_fps=min(refresh, 240),
                gamemode_enabled=gamemode_available,
                gamescope_enabled=False,
                gamescope_mode="disabled",
            )
            reasons.append("Maximum Performance enables GameMode when its service is available")
            reasons.append("The FPS target follows the selected display up to 240 FPS")
            reasons.append("Gamescope scaling remains disabled until the user explicitly enables it")
        elif preset == "balanced":
            category_uses_gamemode = profile.game_category in {
                "competitive", "fast_action", "cinematic"
            }
            values.update(
                user_goal="stable_image",
                target_fps_mode="automatic",
                target_fps=min(refresh, 60),
                gamemode_enabled=gamemode_available and category_uses_gamemode,
                gamescope_enabled=False,
                gamescope_mode="disabled",
            )
            reasons.append("Balanced uses a stable target no higher than 60 FPS")
            reasons.append("GameMode is used only for categories likely to benefit from it")
            reasons.append("No scaling wrapper is added by default")
        elif preset == "quiet":
            values.update(
                user_goal="low_power",
                target_fps_mode="manual",
                target_fps=min(refresh, 45),
                gamemode_enabled=False,
                gamescope_enabled=False,
                gamescope_mode="disabled",
            )
            reasons.append("Quiet disables GameMode and selects a lower 45 FPS target")
            reasons.append("Gamescope remains disabled, so the target is advisory until a limiter is selected")

        if values.get("gamemode_enabled") is False and not gamemode_available:
            conflicts.append("GameMode is unavailable and will not be added")
        if not gamescope_available:
            conflicts.append("Gamescope is unavailable; scaling and its FPS limiter remain disabled")
        if display:
            sources.append("selected display")
        if system_info:
            sources.append("detected system information")
        sources.append("runtime tool availability")

        resolved = replace(profile, **values)
        changes = {
            key: value
            for key, value in values.items()
            if getattr(profile, key) != value
        }
        return OptimizationPresetPlan(
            resolved,
            changes,
            tuple(reasons),
            tuple(dict.fromkeys(sources)),
            tuple(conflicts),
        )


__all__ = [
    "OptimizationAdvisor",
    "OptimizationPresetPlan",
    "OptimizationRecommendation",
    "SessionPerformanceData",
]
