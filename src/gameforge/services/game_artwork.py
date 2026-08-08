"""Resolve one stable local artwork set for every game-facing view."""

from __future__ import annotations

from dataclasses import dataclass, replace
import logging
from pathlib import Path
from typing import Iterable

from gameforge.models import Game
from gameforge.providers.steam import SteamArtworkPaths, find_local_steam_artwork


LOGGER = logging.getLogger(__name__)


def _existing_file(path: Path | None) -> Path | None:
    if path is None:
        return None
    try:
        return path if path.is_file() else None
    except OSError:
        return None


@dataclass(frozen=True, slots=True)
class ResolvedGameArtwork:
    """A game enriched from its cached paths and Steam's local artwork cache."""

    game: Game
    chosen_source: str
    fallback_reason: str


class GameArtworkResolver:
    """Resolve artwork without requiring the game's install directory.

    The resolver intentionally checks paths already stored with the cached game
    before looking in Steam's independent ``appcache/librarycache``.  This makes
    the same result available to Games, Tasks and Updates while an external
    library is disconnected.
    """

    def __init__(self) -> None:
        self._cache: dict[tuple[object, ...], ResolvedGameArtwork] = {}
        self._logged: set[tuple[object, ...]] = set()

    def invalidate(self) -> None:
        """Retry local discovery after a provider refresh."""

        self._cache.clear()

    def resolve(
        self,
        game: Game,
        steam_roots: Iterable[str | Path] = (),
    ) -> ResolvedGameArtwork:
        roots = tuple(dict.fromkeys(Path(root) for root in steam_roots))
        key = (
            game.id,
            str(game.steam_app_id or ""),
            str(game.portrait_artwork_path or ""),
            str(game.header_artwork_path or ""),
            str(game.fallback_artwork_path or ""),
            str(game.cover_asset or ""),
            tuple(str(root) for root in roots),
        )
        cached = self._cache.get(key)
        if cached is not None:
            cached_game = cached.game
            artwork_values = {
                "cover_asset": cached_game.cover_asset,
                "portrait_artwork_path": cached_game.portrait_artwork_path,
                "header_artwork_path": cached_game.header_artwork_path,
                "fallback_artwork_path": cached_game.fallback_artwork_path,
            }
            if all(getattr(game, name) == value for name, value in artwork_values.items()):
                resolved_game = game
            else:
                resolved_game = replace(game, **artwork_values)
            return ResolvedGameArtwork(
                resolved_game,
                cached.chosen_source,
                cached.fallback_reason,
            )

        portrait = _existing_file(game.portrait_artwork_path)
        header = _existing_file(game.header_artwork_path)
        fallback = _existing_file(game.fallback_artwork_path)
        legacy = _existing_file(Path(game.cover_asset)) if game.cover_asset else None
        local = SteamArtworkPaths()
        app_id = str(game.steam_app_id or "").strip()
        if app_id and (portrait is None or header is None or fallback is None):
            local = find_local_steam_artwork(app_id, roots)

        portrait_source = "cached_game" if portrait is not None else ""
        header_source = "cached_game" if header is not None else ""
        fallback_source = "cached_game" if fallback is not None else ""
        if fallback is None and legacy is not None:
            fallback = legacy
            fallback_source = "cached_game_legacy"
        if portrait is None:
            portrait = _existing_file(local.portrait_artwork_path)
            portrait_source = "steam_artwork_cache" if portrait is not None else ""
        if header is None:
            header = _existing_file(local.header_artwork_path)
            header_source = "steam_artwork_cache" if header is not None else ""
        if fallback is None:
            fallback = _existing_file(local.fallback_artwork_path)
            fallback_source = "steam_artwork_cache" if fallback is not None else ""

        chosen = portrait or header or fallback
        chosen_source = (
            portrait_source if portrait is not None else
            header_source if header is not None else
            fallback_source if fallback is not None else
            "placeholder"
        )
        if chosen is not None:
            reason = ""
        elif not app_id:
            reason = "missing_app_id"
        elif not roots:
            reason = "no_steam_artwork_roots"
        else:
            reason = "local_sources_exhausted"

        resolved_cover = str(chosen or game.cover_asset or "")
        if (
            resolved_cover == game.cover_asset
            and portrait == game.portrait_artwork_path
            and header == game.header_artwork_path
            and fallback == game.fallback_artwork_path
        ):
            enriched = game
        else:
            enriched = replace(
                game,
                cover_asset=resolved_cover,
                portrait_artwork_path=portrait,
                header_artwork_path=header,
                fallback_artwork_path=fallback,
            )
        result = ResolvedGameArtwork(enriched, chosen_source, reason)
        self._cache[key] = result
        diagnostic = (
            game.id,
            app_id,
            str(portrait or ""),
            str(header or ""),
            str(fallback or ""),
            chosen_source,
            reason,
        )
        if diagnostic not in self._logged:
            self._logged.add(diagnostic)
            LOGGER.info(
                "Artwork resolution: gameId=%s AppID=%s portrait=%s header=%s "
                "fallback=%s chosen=%s fallback_reason=%s",
                *diagnostic,
            )
        return result


__all__ = ["GameArtworkResolver", "ResolvedGameArtwork"]
