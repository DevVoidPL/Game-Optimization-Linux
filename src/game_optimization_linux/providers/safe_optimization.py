"""Display-only optimization settings for normal mode."""

from __future__ import annotations

from collections.abc import Sequence

from game_optimization_linux.models import Game, Launcher, OptimizationOptions, OptimizationProfile


class PreviewOptimizationProvider:
    """Build previews only; it never changes launcher or system configuration."""

    def profiles(self) -> Sequence[OptimizationProfile]:
        return tuple(OptimizationProfile)

    def defaults_for(self, profile: OptimizationProfile) -> OptimizationOptions:
        return OptimizationOptions(profile=profile)

    def preview_command(self, game: Game, options: OptimizationOptions) -> str:
        if game.launcher is not Launcher.STEAM:
            return f"Steam launch options are unavailable for {game.launcher.value} games."
        tokens: list[str] = []
        if options.gamemode:
            tokens.append("gamemoderun")
        if options.gamescope:
            tokens.extend(("gamescope", "--fullscreen"))
            if options.fps_limit is not None:
                tokens.extend(("--framerate-limit", str(options.fps_limit)))
            if options.adaptive_sync:
                tokens.append("--adaptive-sync")
            if options.cursor_grab:
                tokens.append("--force-grab-cursor")
            tokens.append("--")
        if options.mangohud:
            tokens.append("mangohud")
        tokens.append("%command%")
        return " ".join(tokens)


__all__ = ["PreviewOptimizationProvider"]
