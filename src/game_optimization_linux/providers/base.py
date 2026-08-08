"""Replaceable provider contracts.

Concrete Linux integrations implement these interfaces without exposing
platform details or filesystem operations directly to QML.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Sequence

from game_optimization_linux.models.game import (
    AnalysisReport,
    CompressionEstimate,
    CompressionModeInfo,
    Game,
    OptimizationCompatibility,
    OptimizationOptions,
    TextureOptions,
    TexturePreview,
)
from game_optimization_linux.models.enums import (
    CompressionProfile,
    OptimizationProfile,
    TextureCompatibility,
    TextureMode,
)
from game_optimization_linux.models.system import FilesystemInfo, SystemInfo


class GameProvider(ABC):
    """Discovers games without tying consumers to a specific launcher."""

    @abstractmethod
    def list_games(self) -> Sequence[Game]:
        raise NotImplementedError

    @abstractmethod
    def get_game(self, game_id: str) -> Game | None:
        raise NotImplementedError

    @abstractmethod
    def refresh(self) -> Sequence[Game]:
        raise NotImplementedError

    @abstractmethod
    def add_game(self, game: Game) -> Game:
        raise NotImplementedError


class FilesystemProvider(ABC):
    """Describes filesystems without mutating them."""

    @abstractmethod
    def inspect(self, path: Path) -> FilesystemInfo:
        raise NotImplementedError

    @abstractmethod
    def for_game(self, game: Game) -> FilesystemInfo:
        raise NotImplementedError

    @abstractmethod
    def list_filesystems(
        self,
        *,
        game_paths: Sequence[Path] = (),
        show_system_mounts: bool = False,
    ) -> Sequence[FilesystemInfo]:
        raise NotImplementedError


class CompressionProvider(ABC):
    """Analyzes and estimates compression; it never schedules work itself."""

    @abstractmethod
    def modes(self) -> Sequence[CompressionModeInfo]:
        raise NotImplementedError

    @abstractmethod
    def estimate(
        self, game: Game, profile: CompressionProfile
    ) -> CompressionEstimate:
        raise NotImplementedError

    @abstractmethod
    def analyze(self, game: Game) -> AnalysisReport:
        raise NotImplementedError


class OptimizationProvider(ABC):
    """Creates safe command previews; implementations must not execute them."""

    @abstractmethod
    def profiles(self) -> Sequence[OptimizationProfile]:
        raise NotImplementedError

    @abstractmethod
    def defaults_for(self, profile: OptimizationProfile) -> OptimizationOptions:
        raise NotImplementedError

    @abstractmethod
    def preview_command(self, game: Game, options: OptimizationOptions) -> str:
        raise NotImplementedError

    @abstractmethod
    def compatibility(
        self, game: Game, options: OptimizationOptions
    ) -> OptimizationCompatibility:
        raise NotImplementedError


class TextureEnhancer(ABC):
    """Produces preview metadata without modifying game textures."""

    @abstractmethod
    def modes(self) -> Sequence[TextureMode]:
        raise NotImplementedError

    @abstractmethod
    def compatibility(self, game: Game) -> TextureCompatibility:
        raise NotImplementedError

    @abstractmethod
    def preview(self, game: Game, options: TextureOptions) -> TexturePreview:
        raise NotImplementedError


class SystemProvider(ABC):
    """Supplies system and capability information."""

    @abstractmethod
    def collect(self) -> SystemInfo:
        raise NotImplementedError
