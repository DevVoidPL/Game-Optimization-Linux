"""Deterministic demo providers with no operating-system side effects."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

from gameforge.models.enums import (
    BackupStatus,
    CapabilityStatus,
    CompressionProfile,
    FilesystemType,
    GameStatus,
    Launcher,
    OptimizationProfile,
    SessionType,
    TaskStatus,
    TextureCompatibility,
    TextureMode,
)
from gameforge.models.game import (
    AnalysisReport,
    CompressionEstimate,
    CompressionModeInfo,
    Game,
    OptimizationCompatibility,
    OptimizationOptions,
    TextureOptions,
    TexturePreview,
)
from gameforge.models.system import FilesystemInfo, SystemInfo

from .base import (
    CompressionProvider,
    FilesystemProvider,
    GameProvider,
    OptimizationProvider,
    SystemProvider,
    TextureEnhancer,
)


def demo_games() -> tuple[Game, ...]:
    """Return the four immutable sample games defined in the product brief."""

    return (
        Game(
            id="batman-arkham-knight",
            name="Batman: Arkham Knight",
            launcher=Launcher.STEAM,
            install_path=Path("/demo/steam/Batman Arkham Knight"),
            logical_size_gb=72.4,
            physical_size_gb=72.4,
            filesystem=FilesystemType.BTRFS,
            compression_available=True,
            saved_space_gb=0.0,
            last_task_status=None,
            status=GameStatus.READY,
            cover_asset="cover-batman",
            active_optimization_profile=OptimizationProfile.MAXIMUM_PERFORMANCE,
            backup_status=BackupStatus.AVAILABLE,
            texture_compatibility=TextureCompatibility.FULLY_SUPPORTED,
            has_anticheat=False,
        ),
        Game(
            id="dying-light",
            name="Dying Light",
            launcher=Launcher.STEAM,
            install_path=Path("/demo/steam/Dying Light"),
            logical_size_gb=38.7,
            physical_size_gb=32.4,
            filesystem=FilesystemType.BTRFS,
            compression_available=True,
            saved_space_gb=6.3,
            last_task_status=TaskStatus.COMPLETED,
            status=GameStatus.READY,
            cover_asset="cover-dying-light",
            active_optimization_profile=OptimizationProfile.BALANCED,
            backup_status=BackupStatus.AVAILABLE,
            texture_compatibility=TextureCompatibility.PARTIAL_SUPPORT,
            has_anticheat=True,
        ),
        Game(
            id="cyberpunk-2077",
            name="Cyberpunk 2077",
            launcher=Launcher.HEROIC,
            install_path=Path("/demo/heroic/Cyberpunk 2077"),
            logical_size_gb=86.2,
            physical_size_gb=86.2,
            filesystem=FilesystemType.EXT4,
            compression_available=False,
            saved_space_gb=0.0,
            last_task_status=TaskStatus.FAILED,
            status=GameStatus.NEEDS_ATTENTION,
            cover_asset="cover-cyberpunk",
            active_optimization_profile=OptimizationProfile.QUIET,
            backup_status=BackupStatus.AVAILABLE,
            texture_compatibility=TextureCompatibility.UNSUPPORTED,
            has_anticheat=False,
        ),
        Game(
            id="minecraft",
            name="Minecraft",
            launcher=Launcher.MANUAL,
            install_path=Path("/demo/manual/Minecraft"),
            logical_size_gb=4.8,
            physical_size_gb=4.8,
            filesystem=FilesystemType.BTRFS,
            compression_available=True,
            saved_space_gb=0.0,
            last_task_status=None,
            status=GameStatus.READY,
            cover_asset="cover-minecraft",
            active_optimization_profile=OptimizationProfile.BALANCED,
            backup_status=BackupStatus.AVAILABLE,
            texture_compatibility=TextureCompatibility.FULLY_SUPPORTED,
            has_anticheat=False,
        ),
    )


class DemoGameProvider(GameProvider):
    """In-memory launcher adapter containing only the specified sample games."""

    def __init__(self, games: Iterable[Game] | None = None) -> None:
        initial_games = tuple(games) if games is not None else demo_games()
        self._games: dict[str, Game] = {}
        for game in initial_games:
            if game.id in self._games:
                raise ValueError(f"duplicate game id: {game.id}")
            self._games[game.id] = game

    def list_games(self) -> Sequence[Game]:
        return tuple(self._games.values())

    def get_game(self, game_id: str) -> Game | None:
        return self._games.get(game_id)

    def refresh(self) -> Sequence[Game]:
        # A real adapter would rescan launchers. Demo data is intentionally stable.
        return self.list_games()

    def add_game(self, game: Game) -> Game:
        if game.id in self._games:
            raise ValueError(f"game already exists: {game.id}")
        self._games[game.id] = game
        return game


class DemoFilesystemProvider(FilesystemProvider):
    """Looks up descriptive demo paths without touching the host filesystem."""

    _FILESYSTEMS = (
        FilesystemInfo(
            mount_point=Path("/demo/steam"),
            filesystem=FilesystemType.BTRFS,
            compression_supported=True,
            label="Games",
        ),
        FilesystemInfo(
            mount_point=Path("/demo/heroic"),
            filesystem=FilesystemType.EXT4,
            compression_supported=False,
            label="Heroic Library",
        ),
        FilesystemInfo(
            mount_point=Path("/demo/manual"),
            filesystem=FilesystemType.BTRFS,
            compression_supported=True,
            label="Manual Games",
        ),
    )

    def inspect(self, path: Path) -> FilesystemInfo:
        inspected_path = Path(path)
        # Keep lookup purely lexical and reject parent traversal.  No demo path
        # is resolved against or inspected on the host filesystem.
        if ".." not in inspected_path.parts:
            for filesystem in self._FILESYSTEMS:
                mount_point = filesystem.mount_point
                if inspected_path == mount_point or inspected_path.is_relative_to(
                    mount_point
                ):
                    return filesystem
        return FilesystemInfo(
            mount_point=inspected_path,
            filesystem=FilesystemType.UNKNOWN,
            compression_supported=False,
            label="Demo path (not checked)",
        )

    def for_game(self, game: Game) -> FilesystemInfo:
        return FilesystemInfo(
            mount_point=game.install_path,
            filesystem=game.filesystem,
            compression_supported=game.compression_available,
            label=f"{game.name} (demo)",
        )

    def list_filesystems(
        self,
        *,
        game_paths: Sequence[Path] = (),
        show_system_mounts: bool = False,
    ) -> Sequence[FilesystemInfo]:
        del game_paths, show_system_mounts
        return self._FILESYSTEMS


class DemoCompressionProvider(CompressionProvider):
    """Returns deterministic estimates and reports without reading game files."""

    _MODES = (
        CompressionModeInfo(
            CompressionProfile.FAST,
            "Lowest CPU use and a quick simulated pass.",
            "zstd:1",
        ),
        CompressionModeInfo(
            CompressionProfile.BALANCED,
            "A practical balance between speed and estimated space savings.",
            "zstd:3",
        ),
        CompressionModeInfo(
            CompressionProfile.MAXIMUM,
            "Higher simulated savings with more processing time.",
            "zstd:9",
        ),
        CompressionModeInfo(
            CompressionProfile.AUTO,
            "Selects a conservative level from demo game metadata.",
            "automatic",
        ),
    )
    _RATIOS = {
        CompressionProfile.FAST: 0.90,
        CompressionProfile.BALANCED: 0.82,
        CompressionProfile.MAXIMUM: 0.74,
        CompressionProfile.AUTO: 0.84,
    }

    def modes(self) -> Sequence[CompressionModeInfo]:
        return self._MODES

    def estimate(
        self, game: Game, profile: CompressionProfile
    ) -> CompressionEstimate:
        mode = next(mode for mode in self._MODES if mode.profile is profile)
        if not game.compression_available or game.filesystem is not FilesystemType.BTRFS:
            return CompressionEstimate(
                game_id=game.id,
                profile=profile,
                current_size_gb=game.physical_size_gb,
                estimated_size_gb=game.physical_size_gb,
                estimated_savings_gb=0.0,
                compatible=False,
                filesystem=game.filesystem,
                level=mode.level,
                reason="Btrfs compression is unavailable for this demo game.",
            )

        estimated_size = round(game.physical_size_gb * self._RATIOS[profile], 1)
        return CompressionEstimate(
            game_id=game.id,
            profile=profile,
            current_size_gb=game.physical_size_gb,
            estimated_size_gb=estimated_size,
            estimated_savings_gb=round(game.physical_size_gb - estimated_size, 1),
            compatible=True,
            filesystem=game.filesystem,
            level=mode.level,
            reason="Safe demo estimate; no files were inspected.",
        )

    def analyze(self, game: Game) -> AnalysisReport:
        estimate = self.estimate(game, CompressionProfile.AUTO)
        if estimate.compatible:
            recommendations = (
                "Use the Balanced profile for everyday use.",
                "Create a backup before enabling future real operations.",
            )
            summary = "Demo scan completed; the game is compatible with Btrfs compression."
        else:
            recommendations = (
                "Keep compression disabled on this filesystem.",
                "Re-check compatibility if the library is moved to Btrfs.",
            )
            summary = "Demo scan completed; Btrfs compression is unavailable."
        return AnalysisReport(
            game_id=game.id,
            scanned_size_gb=game.logical_size_gb,
            estimated_savings_gb=estimate.estimated_savings_gb,
            summary=summary,
            recommendations=recommendations,
        )


class DemoOptimizationProvider(OptimizationProvider):
    """Builds a display-only Steam launch-options preview."""

    _NON_STEAM_PREVIEW = (
        "Steam launch options are unavailable for {launcher} games in demo mode."
    )
    _OPTISCALER_WARNING = (
        "OptiScaler is not compatible with every game and needs a separate check."
    )
    _ANTICHEAT_WARNING = "OptiScaler cannot be enabled automatically for anti-cheat games."

    def profiles(self) -> Sequence[OptimizationProfile]:
        return tuple(OptimizationProfile)

    def defaults_for(self, profile: OptimizationProfile) -> OptimizationOptions:
        if profile is OptimizationProfile.MAXIMUM_PERFORMANCE:
            return OptimizationOptions(
                profile=profile,
                gamemode=True,
                gamescope=True,
                mangohud=True,
                adaptive_sync=True,
                cursor_grab=True,
                cpu_performance_profile=True,
                memory_monitoring=True,
            )
        if profile is OptimizationProfile.QUIET:
            return OptimizationOptions(
                profile=profile,
                gamemode=False,
                gamescope=True,
                mangohud=False,
                fps_limit=60,
                adaptive_sync=True,
                memory_monitoring=False,
            )
        return OptimizationOptions(profile=profile)

    def preview_command(self, game: Game, options: OptimizationOptions) -> str:
        if game.launcher is not Launcher.STEAM:
            return self._NON_STEAM_PREVIEW.format(launcher=game.launcher.value)

        # ``game`` is deliberately not interpolated, avoiding user-controlled shell text.
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
        if options.cpu_performance_profile:
            tokens.append("gameforge-cpu-profile-preview")
        if options.memory_monitoring:
            tokens.append("gameforge-memory-monitor-preview")
        # OptiScaler remains absent even from the command preview until a future
        # compatibility provider has explicitly verified the selected game.
        tokens.append("%command%")
        return " ".join(tokens)

    def generate_command_preview(
        self, game: Game, options: OptimizationOptions
    ) -> str:
        """Compatibility alias with an explicit UI-facing name."""

        return self.preview_command(game, options)

    def compatibility(
        self, game: Game, options: OptimizationOptions
    ) -> OptimizationCompatibility:
        warnings: list[str] = []
        if options.optiscaler:
            warnings.append(self._OPTISCALER_WARNING)
            if game.has_anticheat:
                warnings.append(self._ANTICHEAT_WARNING)
        return OptimizationCompatibility(
            compatible=not options.optiscaler,
            warnings=tuple(warnings),
        )


class DemoTextureEnhancer(TextureEnhancer):
    """Provides local placeholder preview identifiers only."""

    def modes(self) -> Sequence[TextureMode]:
        return tuple(TextureMode)

    def compatibility(self, game: Game) -> TextureCompatibility:
        return game.texture_compatibility

    def preview(self, game: Game, options: TextureOptions) -> TexturePreview:
        scale_factor = {"Auto": 1.5, "2x": 2.0, "4x": 4.0}[options.scale]
        estimate = min(
            options.max_output_size_gb,
            round(game.logical_size_gb * 0.08 * scale_factor, 1),
        )
        return TexturePreview(
            game_id=game.id,
            compatibility=game.texture_compatibility,
            source_asset="texture-preview-before",
            enhanced_asset="texture-preview-after",
            estimated_output_size_gb=estimate,
        )


class DemoSystemProvider(SystemProvider):
    """Returns plausible but clearly labelled demonstration system data."""

    def collect(self) -> SystemInfo:
        return SystemInfo(
            distribution="Arch Linux (Demo)",
            kernel="6.12.0-demo",
            desktop_environment="KDE Plasma 6",
            session_type=SessionType.WAYLAND,
            cpu="AMD Ryzen 7 7800X3D (Demo)",
            cpu_cores=8,
            cpu_threads=16,
            gpu="AMD Radeon RX 7800 XT (Demo)",
            gpu_driver="Mesa RADV (Demo)",
            ram_gb=32.0,
            vram_gb=16.0,
            filesystems=tuple(DemoFilesystemProvider().list_filesystems()),
            capabilities={
                "GameMode": CapabilityStatus.AVAILABLE,
                "Gamescope": CapabilityStatus.AVAILABLE,
                "MangoHud": CapabilityStatus.MISSING,
                "Btrfs tools": CapabilityStatus.AVAILABLE,
                "Polkit": CapabilityStatus.NOT_CHECKED,
                "Vulkan": CapabilityStatus.AVAILABLE,
                "OptiScaler": CapabilityStatus.GAME_DEPENDENT,
            },
            demo=True,
        )

    def get_system_info(self) -> SystemInfo:
        """UI-friendly alias for :meth:`collect`."""

        return self.collect()


# ``Mock`` names make the replaceable nature explicit for integrations that use
# that terminology. They intentionally point to the same side-effect-free types.
MockGameProvider = DemoGameProvider
MockFilesystemProvider = DemoFilesystemProvider
MockCompressionProvider = DemoCompressionProvider
MockOptimizationProvider = DemoOptimizationProvider
MockTextureEnhancer = DemoTextureEnhancer
MockSystemProvider = DemoSystemProvider
