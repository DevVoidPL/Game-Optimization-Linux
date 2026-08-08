"""Conservative, explainable optimization recommendations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gameforge.models import GameOptimizationProfile
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


class OptimizationAdvisor:
    def recommend(
        self,
        profile: GameOptimizationProfile,
        display: DisplayProfile | None,
        measurements: SessionPerformanceData | None = None,
    ) -> OptimizationRecommendation:
        refresh = max(30, int(round(display.refresh_rate if display else 60)))
        category = profile.game_category
        reasons = [f"Game category: {category}"]
        if display:
            reasons.append(f"Selected display: {display.width}×{display.height} at {refresh} Hz")
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


__all__ = ["OptimizationAdvisor", "OptimizationRecommendation", "SessionPerformanceData"]
