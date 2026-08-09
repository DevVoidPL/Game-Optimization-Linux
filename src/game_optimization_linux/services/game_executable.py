"""Conservative main-executable discovery scoped to one installed game."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import os
from pathlib import Path
import re
from typing import Any

from game_optimization_linux.models import Game


_IGNORED_PARTS = (
    "unins", "uninstall", "crash", "reporter", "redist", "redistributable",
    "setup", "installer", "config", "configuration", "benchmark", "diagnostic",
    "support", "supporttool", "helper", "eac", "easyanticheat", "battleye", "dotnet",
    "vcredist", "dxsetup",
)
_PRUNED_DIRECTORIES = {
    "_commonredist", "redist", "redistributables", "installers", "support",
    "easyanticheat", "battleye",
}
_WORD = re.compile(r"[a-z0-9]+")


def _normalized(value: str) -> str:
    return "".join(_WORD.findall(value.casefold()))


@dataclass(frozen=True, slots=True)
class ExecutableCandidate:
    relative_path: str
    name: str
    wine: bool
    score: int
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "relativePath": self.relative_path,
            "name": self.name,
            "wine": self.wine,
            "score": self.score,
            "reasons": list(self.reasons),
            "label": self.relative_path,
        }


@dataclass(frozen=True, slots=True)
class ExecutableResolution:
    status: str
    candidates: tuple[ExecutableCandidate, ...] = ()
    selected: ExecutableCandidate | None = None
    automatic: bool = False
    message: str = ""

    @property
    def reliable(self) -> bool:
        return self.selected is not None and self.status in {"selected", "confident"}


class GameExecutableResolver:
    """Find likely native or Proton entry points without leaving the game root."""

    def __init__(self, *, maximum_files: int = 50000) -> None:
        self.maximum_files = max(1, int(maximum_files))

    @staticmethod
    def _root(game: Game) -> Path | None:
        try:
            root = game.install_path.resolve(strict=True)
        except OSError:
            return None
        return root if root.is_dir() else None

    def validate_selected(
        self, game: Game, selected_path: str
    ) -> ExecutableCandidate | None:
        """Validate a saved/manual executable independently of result limits.

        A manual selection is a durable per-game decision.  It must not become
        invalid merely because a later scan found enough other executables to
        push it outside the 30 candidates shown in the UI.
        """

        root = self._root(game)
        text = str(selected_path or "").strip()
        if root is None or not text or "\0" in text:
            return None
        raw = Path(text)
        candidate_path = raw if raw.is_absolute() else root / raw
        try:
            if candidate_path.is_symlink():
                return None
            resolved = candidate_path.resolve(strict=True)
            relative = resolved.relative_to(root)
        except (OSError, ValueError):
            return None
        if not resolved.is_file():
            return None
        wine = resolved.suffix.casefold() == ".exe"
        if not wine:
            try:
                executable = os.access(resolved, os.X_OK)
            except OSError:
                executable = False
            if not executable or resolved.suffix.casefold() in {".so", ".dll", ".sh", ".py"}:
                return None
        selected = self._score(game, root, resolved, wine=wine)
        # Keep the canonical path relative to the game root in persisted data.
        return ExecutableCandidate(
            relative.as_posix(), selected.name, selected.wine,
            selected.score, selected.reasons,
        )

    def _files(self, root: Path) -> list[Path]:
        files: list[Path] = []
        visited = 0
        for directory, names, filenames in os.walk(root, followlinks=False):
            names[:] = [
                name for name in names
                if name.casefold() not in _PRUNED_DIRECTORIES and not name.startswith(".")
            ]
            for filename in filenames:
                visited += 1
                if visited > self.maximum_files:
                    return files
                if "\n" in filename or "\r" in filename or "\0" in filename:
                    continue
                path = Path(directory) / filename
                try:
                    if path.is_symlink() or not path.is_file():
                        continue
                except OSError:
                    continue
                files.append(path)
        return files

    @staticmethod
    def _ignored(path: Path) -> bool:
        folded = "/".join(part.casefold() for part in path.parts)
        return any(part in folded for part in _IGNORED_PARTS)

    @staticmethod
    def _score(game: Game, root: Path, path: Path, *, wine: bool) -> ExecutableCandidate:
        relative = path.relative_to(root).as_posix()
        stem = path.stem if wine else path.name
        candidate = _normalized(stem)
        game_name = _normalized(game.name)
        directory_name = _normalized(root.name)
        score = 0
        reasons: list[str] = []
        if candidate and candidate == game_name:
            score += 110
            reasons.append("matches_game")
        if candidate and candidate == directory_name:
            score += 100
            reasons.append("matches_directory")
        similarity = max(
            SequenceMatcher(None, candidate, game_name).ratio() if candidate and game_name else 0,
            SequenceMatcher(None, candidate, directory_name).ratio()
            if candidate and directory_name else 0,
        )
        score += int(similarity * 45)
        if "win64-shipping" in path.name.casefold():
            score += 80
            reasons.append("unreal_shipping")
        folded_parts = {part.casefold() for part in path.parts}
        if "binaries" in folded_parts and folded_parts.intersection({"win64", "wingdk"}):
            score += 22
            reasons.append("game_binary_directory")
        # Unreal's Engine directory often contains helper executables whose
        # names also end in Shipping.exe.  Keep them as a last-resort fallback,
        # but strongly prefer the executable under the game's own Binaries
        # directory.
        if "engine" in folded_parts:
            score -= 90
            reasons.append("engine_tool_penalty")
        depth = len(path.relative_to(root).parts) - 1
        score -= min(depth * 3, 18)
        return ExecutableCandidate(relative, path.name, wine, score, tuple(reasons))

    def resolve(self, game: Game, selected_relative_path: str = "") -> ExecutableResolution:
        root = self._root(game)
        if root is None:
            return ExecutableResolution("not_found", message="Game directory is unavailable")
        saved_candidate = (
            self.validate_selected(game, selected_relative_path)
            if selected_relative_path else None
        )
        files = self._files(root)
        proton_files = [
            path for path in files
            if path.suffix.casefold() == ".exe" and not self._ignored(path.relative_to(root))
        ]
        native_files = []
        for path in files:
            relative = path.relative_to(root)
            if len(relative.parts) > 4 or self._ignored(relative):
                continue
            try:
                executable = os.access(path, os.X_OK)
            except OSError:
                executable = False
            if (
                executable
                and path.suffix.casefold()
                not in {".exe", ".so", ".dll", ".sh", ".py"}
            ):
                native_files.append(path)
        executable_files = (
            [*proton_files, *native_files]
            if game.data_source.casefold() == "local"
            else (proton_files or native_files)
        )
        candidates = tuple(
            sorted(
                (
                    self._score(
                        game,
                        root,
                        path,
                        wine=path.suffix.casefold() == ".exe",
                    )
                    for path in executable_files
                ),
                key=lambda item: (-item.score, item.relative_path.casefold()),
            )[:30]
        )
        if selected_relative_path:
            if saved_candidate is not None:
                visible_candidates = candidates
                if all(
                    item.relative_path != saved_candidate.relative_path
                    for item in visible_candidates
                ):
                    visible_candidates = (*visible_candidates, saved_candidate)
                return ExecutableResolution(
                    "selected", tuple(visible_candidates), saved_candidate, automatic=False,
                    message="Saved executable selected",
                )
            return ExecutableResolution(
                "saved_invalid", candidates, message="Saved executable is no longer available"
            )
        if not candidates:
            return ExecutableResolution("not_found", message="No game executable was detected")
        first = candidates[0]
        second_score = candidates[1].score if len(candidates) > 1 else -1000
        if len(candidates) == 1 or (
            first.score >= 65 and first.score - second_score >= 18
        ):
            return ExecutableResolution(
                "confident", candidates, first, automatic=True,
                message="Game executable detected",
            )
        return ExecutableResolution(
            "ambiguous", candidates,
            message="Choose the main game executable",
        )


__all__ = [
    "ExecutableCandidate",
    "ExecutableResolution",
    "GameExecutableResolver",
]
