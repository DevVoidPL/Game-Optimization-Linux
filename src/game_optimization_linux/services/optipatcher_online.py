"""Official OptiPatcher release discovery and verified asset downloads."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
import tempfile
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


OPTIPATCHER_REPOSITORY = "optiscaler/OptiPatcher"
OPTIPATCHER_RELEASES_URL = (
    "https://api.github.com/repos/optiscaler/OptiPatcher/releases"
)
_VERSION_RE = re.compile(r"(?i)(?:^|[_-])v?(\d+(?:\.\d+){1,3})(?:$|[._-])")
_ASSET_RE = re.compile(r"(?i)^OptiPatcher(?:_v\d+(?:\.\d+){1,3})?\.asi$")


class OptiPatcherOnlineError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class OptiPatcherRelease:
    tag_name: str
    version: str
    html_url: str
    asset_name: str
    download_url: str
    size: int
    sha256: str = ""


class OptiPatcherReleaseClient:
    def __init__(
        self,
        cache_root: Path,
        *,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.cache_root = Path(cache_root)
        self.opener = opener or urlopen

    def _request(self, url: str) -> bytes:
        request = Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "Game-Optimization-Linux",
            },
        )
        try:
            with self.opener(request, timeout=30) as response:
                if int(getattr(response, "status", 200)) != 200:
                    raise OptiPatcherOnlineError(
                        f"OptiPatcher request returned HTTP {response.status}"
                    )
                return response.read()
        except HTTPError as error:
            raise OptiPatcherOnlineError(
                f"OptiPatcher request returned HTTP {error.code}"
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            raise OptiPatcherOnlineError(
                f"could not access official OptiPatcher release data: {error}"
            ) from error

    @staticmethod
    def parse_releases(payload: object) -> OptiPatcherRelease:
        if not isinstance(payload, list):
            raise OptiPatcherOnlineError("OptiPatcher release response is invalid")
        candidates: list[OptiPatcherRelease] = []
        for raw in payload:
            if not isinstance(raw, Mapping) or raw.get("draft") or raw.get("prerelease"):
                continue
            tag = str(raw.get("tag_name", "")).strip()
            version_match = _VERSION_RE.search(tag)
            if not version_match:
                continue
            for asset in raw.get("assets", ()):
                if not isinstance(asset, Mapping):
                    continue
                name = str(asset.get("name", "")).strip()
                url = str(asset.get("browser_download_url", "")).strip()
                if not _ASSET_RE.fullmatch(name) or not url.startswith(
                    "https://github.com/optiscaler/OptiPatcher/releases/download/"
                ):
                    continue
                digest = str(asset.get("digest", "")).partition(":")[2].casefold()
                candidates.append(
                    OptiPatcherRelease(
                        tag,
                        version_match.group(1),
                        str(raw.get("html_url", "")),
                        name,
                        url,
                        int(asset.get("size", 0)),
                        digest if re.fullmatch(r"[0-9a-f]{64}", digest) else "",
                    )
                )
        if not candidates:
            raise OptiPatcherOnlineError(
                "no official OptiPatcher release asset was found"
            )
        return max(
            candidates,
            key=lambda item: tuple(int(part) for part in item.version.split(".")),
        )

    def latest_release(self, *, force_refresh: bool = False) -> OptiPatcherRelease:
        cache = self.cache_root / "latest.json"
        if cache.is_file() and not force_refresh:
            try:
                return OptiPatcherRelease(**json.loads(cache.read_text()))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass
        try:
            release = self.parse_releases(
                json.loads(self._request(OPTIPATCHER_RELEASES_URL))
            )
        except (json.JSONDecodeError, TypeError) as error:
            raise OptiPatcherOnlineError(
                "OptiPatcher release response is not valid JSON"
            ) from error
        self.cache_root.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(asdict(release), sort_keys=True), encoding="utf-8")
        return release

    def ensure_asset(self, release: OptiPatcherRelease) -> tuple[Path, str]:
        target_dir = self.cache_root / "assets" / release.version
        target = target_dir / release.asset_name
        if target.is_file() and (
            release.size <= 0 or target.stat().st_size == release.size
        ):
            digest = self._hash_file(target)
            if not release.sha256 or digest == release.sha256:
                return target, digest
        target_dir.mkdir(parents=True, exist_ok=True)
        request = Request(
            release.download_url,
            headers={"Accept": "application/octet-stream", "User-Agent": "Game-Optimization-Linux"},
        )
        with tempfile.NamedTemporaryFile(
            dir=target_dir, prefix=f".{release.asset_name}.", delete=False
        ) as output:
            temporary = Path(output.name)
            try:
                with self.opener(request, timeout=60) as response:
                    if int(getattr(response, "status", 200)) != 200:
                        raise OptiPatcherOnlineError("OptiPatcher asset download failed")
                    digest = sha256()
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
                        digest.update(chunk)
                output.flush()
                actual = digest.hexdigest()
                if release.size > 0 and temporary.stat().st_size != release.size:
                    raise OptiPatcherOnlineError("OptiPatcher asset size does not match metadata")
                if release.sha256 and actual != release.sha256:
                    raise OptiPatcherOnlineError("OptiPatcher asset checksum does not match metadata")
                temporary.replace(target)
                return target, actual
            except (HTTPError, URLError, TimeoutError, OSError) as error:
                raise OptiPatcherOnlineError(f"could not download official OptiPatcher: {error}") from error
            finally:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()
