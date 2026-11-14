"""Validated Steam library assets, cached outside Steam's own installation.

Only the library scan worker downloads missing/undersized images. Good local
artwork stays untouched. Failed lookups are retried after a day, not per card.
"""
from dataclasses import replace
import json
import logging
from pathlib import Path
import tempfile
import time
from urllib.request import Request, urlopen

from ..providers.artwork import best_local_image, image_dimensions
from ..providers.steam import SteamArtworkPaths

LOGGER = logging.getLogger(__name__)
_MAX_BYTES = 8 * 1024 * 1024
_ASSETS = {
    "portrait_artwork_path": ("library_600x900_2x.jpg", "library_600x900.jpg", "library_capsule.jpg"),
    "header_artwork_path": ("library_hero.jpg", "library_header.jpg"),
}


class SteamArtworkCache:
    def __init__(self, root: Path, *, opener=urlopen, clock=time.time):
        self.root, self.opener, self.clock = Path(root), opener, clock

    @staticmethod
    def _suitable(path, field):
        width, height = image_dimensions(path) if path is not None else (0, 0)
        if not height:
            return False
        if field == "portrait_artwork_path":
            return width >= 300 and height >= 450 and 0.5 <= width / height <= 0.85
        return width >= 460 and height >= 150 and 1.8 <= width / height <= 4

    def cached(self, app_id, local):
        if not str(app_id).isascii() or not str(app_id).isdecimal():
            return local
        values = {}
        for field, names in _ASSETS.items():
            candidates = [getattr(local, field)]
            candidates.extend(self.root / str(app_id) / name for name in names)
            valid = [path for path in candidates if path is not None and self._suitable(path, field)]
            values[field] = best_local_image(valid) if valid else getattr(local, field)
        return replace(local, **values)

    def resolve(self, app_id: str, local: SteamArtworkPaths) -> SteamArtworkPaths:
        app_id = str(app_id)
        if not app_id.isascii() or not app_id.isdecimal():
            return local
        result = self.cached(app_id, local)
        directory = self.root / app_id
        for field, names in _ASSETS.items():
            if self._suitable(getattr(result, field), field):
                continue
            marker = directory / (field + ".retry.json")
            try:
                if self.clock() - json.loads(marker.read_text())["checked"] < 86400:
                    continue
            except (OSError, ValueError, KeyError, TypeError):
                pass
            try:
                directory.mkdir(parents=True, exist_ok=True)
                for name in names:
                    temporary = None
                    try:
                        request = Request(f"https://cdn.akamai.steamstatic.com/steam/apps/{app_id}/{name}",
                                          headers={"User-Agent": "GameOpti/2.3.0 artwork"})
                        with self.opener(request, timeout=4) as response:
                            data = response.read(_MAX_BYTES + 1)
                        if len(data) > _MAX_BYTES:
                            raise ValueError("Artwork exceeds size limit")
                        with tempfile.NamedTemporaryFile(dir=directory, suffix=".jpg", delete=False) as output:
                            temporary = Path(output.name)
                            output.write(data)
                        if not self._suitable(temporary, field):
                            raise ValueError("Artwork is unreadable, too small or has the wrong aspect ratio")
                        destination = directory / name
                        temporary.replace(destination)
                        result = replace(result, **{field: destination})
                        break
                    except (OSError, ValueError) as error:
                        LOGGER.debug("Steam artwork %s/%s: %s", app_id, name, error)
                    finally:
                        if temporary is not None:
                            temporary.unlink(missing_ok=True)
                marker.write_text(json.dumps({"checked": self.clock()}))
            except OSError as error:
                LOGGER.debug("Cannot cache Steam artwork: %s", error)
        return result