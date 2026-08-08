"""Official OptiScaler release discovery and verified archive caching.

This module intentionally has no knowledge of games or installation targets.
It obtains release metadata only from the official GitHub repository and
returns a validated local archive that can be handed to ``OptiScalerService``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import time
from typing import Any, BinaryIO, Final
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from game_optimization_linux.config import CACHE_DIR

from .archive_reader import ArchiveReadError, open_archive


OFFICIAL_RELEASES_URL: Final = (
    "https://api.github.com/repos/optiscaler/OptiScaler/releases"
)
OFFICIAL_REPOSITORY: Final = "optiscaler/OptiScaler"
SUPPORTED_ARCHIVE_SUFFIXES: Final = (".7z", ".zip")
METADATA_CACHE_SCHEMA_VERSION: Final = 1
ARCHIVE_CACHE_SCHEMA_VERSION: Final = 1
DEFAULT_METADATA_MAX_AGE_SECONDS: Final = 15 * 60
DEFAULT_MAX_METADATA_BYTES: Final = 2 * 1024 * 1024
DEFAULT_MAX_ARCHIVE_BYTES: Final = 1024 * 1024 * 1024
DOWNLOAD_CHUNK_SIZE: Final = 1024 * 1024


class OptiScalerOnlineError(RuntimeError):
    """Base error for official release discovery and caching."""


class OptiScalerNetworkError(OptiScalerOnlineError):
    """The official GitHub service could not be reached."""


class OptiScalerMetadataError(OptiScalerOnlineError):
    """GitHub returned unusable or unsupported release metadata."""


class OptiScalerDownloadError(OptiScalerOnlineError):
    """The selected release archive could not be safely downloaded."""


class OptiScalerCacheError(OptiScalerOnlineError):
    """The application cache could not be read or updated."""


@dataclass(frozen=True, slots=True)
class OptiScalerReleaseAsset:
    """One supported archive belonging to an official release."""

    name: str
    download_url: str
    size: int
    content_type: str = ""
    digest: str = ""

    @property
    def suffix(self) -> str:
        return Path(self.name).suffix.casefold()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "download_url": self.download_url,
            "size": self.size,
            "content_type": self.content_type,
            "digest": self.digest,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "OptiScalerReleaseAsset":
        try:
            asset = cls(
                name=str(raw["name"]),
                download_url=str(raw["download_url"]),
                size=int(raw["size"]),
                content_type=str(raw.get("content_type", "")),
                digest=str(raw.get("digest") or ""),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise OptiScalerMetadataError(
                "cached OptiScaler asset metadata is incomplete"
            ) from error
        _validate_asset(asset)
        return asset


@dataclass(frozen=True, slots=True)
class OptiScalerRelease:
    """Latest stable release and the archive selected for installation."""

    tag_name: str
    version: str
    html_url: str
    published_at: str
    asset: OptiScalerReleaseAsset
    source: str = "network"
    stale: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "tag_name": self.tag_name,
            "version": self.version,
            "html_url": self.html_url,
            "published_at": self.published_at,
            "asset": self.asset.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "OptiScalerRelease":
        try:
            asset_raw = raw["asset"]
            if not isinstance(asset_raw, Mapping):
                raise TypeError("asset must be an object")
            release = cls(
                tag_name=str(raw["tag_name"]),
                version=str(raw["version"]),
                html_url=str(raw["html_url"]),
                published_at=str(raw.get("published_at", "")),
                asset=OptiScalerReleaseAsset.from_dict(asset_raw),
                source="cache",
            )
        except (KeyError, TypeError, ValueError) as error:
            raise OptiScalerMetadataError(
                "cached OptiScaler release metadata is incomplete"
            ) from error
        _validate_release(release)
        return release


@dataclass(frozen=True, slots=True)
class CachedOptiScalerArchive:
    """A fully downloaded and validated archive in the XDG cache."""

    path: Path
    sha256: str
    size: int
    release: OptiScalerRelease
    from_cache: bool = False


def _is_official_release_download_url(value: str) -> bool:
    parsed = urlparse(str(value))
    return (
        parsed.scheme == "https"
        and parsed.hostname is not None
        and parsed.hostname.casefold() == "github.com"
        and parsed.path.casefold().startswith(
            "/optiscaler/optiscaler/releases/download/"
        )
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
    )


def _is_official_release_page_url(value: str) -> bool:
    parsed = urlparse(str(value))
    return (
        parsed.scheme == "https"
        and parsed.hostname is not None
        and parsed.hostname.casefold() == "github.com"
        and parsed.path.casefold().startswith(
            "/optiscaler/optiscaler/releases/"
        )
        and not parsed.username
        and not parsed.password
    )


def _validate_asset(asset: OptiScalerReleaseAsset) -> None:
    if (
        not asset.name
        or Path(asset.name).name != asset.name
        or "/" in asset.name
        or "\\" in asset.name
        or asset.suffix not in SUPPORTED_ARCHIVE_SUFFIXES
    ):
        raise OptiScalerMetadataError("release asset has an unsafe archive name")
    if asset.size <= 0:
        raise OptiScalerMetadataError("release asset has no valid size")
    if not _is_official_release_download_url(asset.download_url):
        raise OptiScalerMetadataError(
            "release asset does not belong to the official OptiScaler repository"
        )
    if asset.digest and not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", asset.digest):
        raise OptiScalerMetadataError("release asset has an invalid digest")


def _validate_release(release: OptiScalerRelease) -> None:
    if not release.tag_name.strip() or not release.version.strip():
        raise OptiScalerMetadataError("release version is missing")
    if not _is_official_release_page_url(release.html_url):
        raise OptiScalerMetadataError(
            "release page does not belong to the official OptiScaler repository"
        )
    _validate_asset(release.asset)


def _normalized_version(tag_name: str) -> str:
    tag = str(tag_name or "").strip()
    return tag[1:] if tag[:1].casefold() == "v" else tag


def _asset_priority(asset: OptiScalerReleaseAsset) -> tuple[int, int, str]:
    folded = asset.name.casefold()
    penalty_markers = ("debug", "symbols", "source", "pdb", "installer")
    return (
        1 if asset.suffix == ".7z" else 0,
        -sum(marker in folded for marker in penalty_markers),
        folded,
    )


def _parse_asset(raw: Mapping[str, Any]) -> OptiScalerReleaseAsset | None:
    try:
        name = str(raw["name"])
        url = str(raw["browser_download_url"])
        size = int(raw["size"])
    except (KeyError, TypeError, ValueError):
        return None
    candidate = OptiScalerReleaseAsset(
        name=name,
        download_url=url,
        size=size,
        content_type=str(raw.get("content_type", "")),
        digest=str(raw.get("digest") or ""),
    )
    try:
        _validate_asset(candidate)
    except OptiScalerMetadataError:
        return None
    return candidate


def parse_latest_stable_release(payload: object) -> OptiScalerRelease:
    """Select the first stable release with a supported official asset."""

    if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes)):
        raise OptiScalerMetadataError("GitHub release metadata must be a JSON array")
    stable_release_seen = False
    for raw_release in payload:
        if not isinstance(raw_release, Mapping):
            continue
        if bool(raw_release.get("draft")) or bool(raw_release.get("prerelease")):
            continue
        stable_release_seen = True
        raw_assets = raw_release.get("assets", ())
        if not isinstance(raw_assets, Sequence) or isinstance(
            raw_assets, (str, bytes)
        ):
            continue
        candidates = [
            candidate
            for item in raw_assets
            if isinstance(item, Mapping)
            if (candidate := _parse_asset(item)) is not None
        ]
        if not candidates:
            continue
        selected = max(candidates, key=_asset_priority)
        tag_name = str(raw_release.get("tag_name", "")).strip()
        release = OptiScalerRelease(
            tag_name=tag_name,
            version=_normalized_version(tag_name),
            html_url=str(raw_release.get("html_url", "")),
            published_at=str(raw_release.get("published_at", "")),
            asset=selected,
        )
        _validate_release(release)
        return release
    if stable_release_seen:
        raise OptiScalerMetadataError(
            "the latest stable OptiScaler releases have no supported ZIP or 7z asset"
        )
    raise OptiScalerMetadataError("no stable OptiScaler release is available")


def _atomic_json_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except OSError as error:
        raise OptiScalerCacheError(f"could not update OptiScaler cache: {error}") from error
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _hash_file(path: Path) -> str:
    digest = sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(DOWNLOAD_CHUNK_SIZE):
                digest.update(chunk)
    except OSError as error:
        raise OptiScalerCacheError(
            f"could not verify cached OptiScaler archive: {error}"
        ) from error
    return digest.hexdigest()


def _cache_slug(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return sanitized[:100] or "release"


class OptiScalerReleaseClient:
    """Fetch official release metadata and maintain a verified XDG cache."""

    def __init__(
        self,
        cache_root: Path = CACHE_DIR / "optiscaler-online",
        *,
        opener: Callable[..., BinaryIO] = urlopen,
        timeout: float = 20.0,
        metadata_max_age: int = DEFAULT_METADATA_MAX_AGE_SECONDS,
        max_metadata_bytes: int = DEFAULT_MAX_METADATA_BYTES,
        max_archive_bytes: int = DEFAULT_MAX_ARCHIVE_BYTES,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.cache_root = Path(cache_root)
        self._opener = opener
        self.timeout = max(0.1, float(timeout))
        self.metadata_max_age = max(0, int(metadata_max_age))
        self.max_metadata_bytes = max(1024, int(max_metadata_bytes))
        self.max_archive_bytes = max(1024, int(max_archive_bytes))
        self._clock = clock

    @property
    def metadata_cache_path(self) -> Path:
        return self.cache_root / "latest-stable.json"

    def _open(self, request: Request) -> BinaryIO:
        return self._opener(request, timeout=self.timeout)

    def _load_cached_release(self) -> tuple[OptiScalerRelease, float] | None:
        path = self.metadata_cache_path
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, Mapping):
                raise TypeError("cache root must be an object")
            if int(raw.get("schema_version", 0)) != METADATA_CACHE_SCHEMA_VERSION:
                return None
            if str(raw.get("repository", "")) != OFFICIAL_REPOSITORY:
                return None
            cached_at = float(raw["cached_at"])
            release_raw = raw["release"]
            if not isinstance(release_raw, Mapping):
                raise TypeError("release must be an object")
            release = OptiScalerRelease.from_dict(release_raw)
        except (
            OSError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
            OptiScalerMetadataError,
        ):
            return None
        return release, cached_at

    def cached_release(self) -> OptiScalerRelease | None:
        """Read validated cached metadata without making a network request."""

        cached = self._load_cached_release()
        if cached is None:
            return None
        release, cached_at = cached
        age = max(0.0, self._clock() - cached_at)
        return replace(
            release,
            source="cache",
            stale=age > self.metadata_max_age,
        )

    def _save_release(self, release: OptiScalerRelease) -> None:
        _atomic_json_write(
            self.metadata_cache_path,
            {
                "schema_version": METADATA_CACHE_SCHEMA_VERSION,
                "repository": OFFICIAL_REPOSITORY,
                "cached_at": self._clock(),
                "release": release.to_dict(),
            },
        )

    def _fetch_release_metadata(self) -> OptiScalerRelease:
        request = Request(
            OFFICIAL_RELEASES_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "Game-Optimization-Linux",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method="GET",
        )
        try:
            with self._open(request) as response:
                status = int(getattr(response, "status", 200))
                if status != 200:
                    raise OptiScalerMetadataError(
                        f"GitHub release metadata returned HTTP {status}"
                    )
                final_url_getter = getattr(response, "geturl", None)
                if callable(final_url_getter):
                    final_url = urlparse(str(final_url_getter()))
                    if (
                        final_url.scheme != "https"
                        or (final_url.hostname or "").casefold() != "api.github.com"
                    ):
                        raise OptiScalerMetadataError(
                            "GitHub release metadata redirected outside the official API"
                        )
                body = response.read(self.max_metadata_bytes + 1)
        except HTTPError as error:
            raise OptiScalerMetadataError(
                f"GitHub release metadata returned HTTP {error.code}"
            ) from error
        except (URLError, TimeoutError, ConnectionError, OSError) as error:
            raise OptiScalerNetworkError(
                f"could not reach the official OptiScaler releases: {error}"
            ) from error
        if len(body) > self.max_metadata_bytes:
            raise OptiScalerMetadataError("GitHub release metadata is too large")
        try:
            raw = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise OptiScalerMetadataError(
                "GitHub returned invalid OptiScaler release metadata"
            ) from error
        return parse_latest_stable_release(raw)

    def latest_release(
        self,
        *,
        force_refresh: bool = False,
        allow_stale_cache: bool = True,
    ) -> OptiScalerRelease:
        """Return the latest stable release, using cache only when appropriate."""

        cached = self._load_cached_release()
        if cached is not None and not force_refresh:
            release, cached_at = cached
            age = max(0.0, self._clock() - cached_at)
            if age <= self.metadata_max_age:
                return replace(release, source="cache", stale=False)
        try:
            release = self._fetch_release_metadata()
            self._save_release(release)
            return release
        except (OptiScalerNetworkError, OptiScalerMetadataError):
            if cached is not None and allow_stale_cache:
                return replace(cached[0], source="cache", stale=True)
            raise

    def _archive_paths(self, release: OptiScalerRelease) -> tuple[Path, Path]:
        version_directory = (
            self.cache_root
            / "archives"
            / f"{_cache_slug(release.version)}-{sha256(release.tag_name.encode()).hexdigest()[:10]}"
        )
        archive_path = version_directory / release.asset.name
        return archive_path, archive_path.with_name(f"{archive_path.name}.json")

    @staticmethod
    def _validate_archive_payload(path: Path) -> None:
        try:
            reader = open_archive(path)
        except ArchiveReadError as error:
            raise OptiScalerDownloadError(
                f"downloaded OptiScaler archive is invalid: {error}"
            ) from error
        files = [
            PurePosixPath(entry.relative_path)
            for entry in reader.entries
            if not entry.is_directory
        ]
        dlls = [item for item in files if item.name.casefold() == "optiscaler.dll"]
        if len(dlls) != 1:
            raise OptiScalerDownloadError(
                "downloaded archive must contain exactly one OptiScaler.dll"
            )
        if not any(
            item.parent == dlls[0].parent
            and item.name.casefold() == "optiscaler.ini"
            for item in files
        ):
            raise OptiScalerDownloadError(
                "downloaded archive does not contain OptiScaler.ini"
            )

    def _cached_archive(
        self,
        release: OptiScalerRelease,
        archive_path: Path,
        record_path: Path,
    ) -> CachedOptiScalerArchive | None:
        if not archive_path.is_file() or not record_path.is_file():
            return None
        try:
            raw = json.loads(record_path.read_text(encoding="utf-8"))
            if not isinstance(raw, Mapping):
                return None
            if int(raw.get("schema_version", 0)) != ARCHIVE_CACHE_SCHEMA_VERSION:
                return None
            if str(raw.get("repository", "")) != OFFICIAL_REPOSITORY:
                return None
            if str(raw.get("tag_name", "")) != release.tag_name:
                return None
            if str(raw.get("asset_name", "")) != release.asset.name:
                return None
            if str(raw.get("download_url", "")) != release.asset.download_url:
                return None
            expected_size = int(raw.get("size", -1))
            expected_hash = str(raw.get("sha256", ""))
            if (
                expected_size != release.asset.size
                or archive_path.stat().st_size != expected_size
                or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
                or (
                    bool(release.asset.digest)
                    and expected_hash
                    != release.asset.digest.partition(":")[2].casefold()
                )
                or _hash_file(archive_path) != expected_hash
            ):
                return None
            self._validate_archive_payload(archive_path)
        except (
            OSError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
            OptiScalerCacheError,
            OptiScalerDownloadError,
        ):
            return None
        return CachedOptiScalerArchive(
            path=archive_path,
            sha256=expected_hash,
            size=expected_size,
            release=release,
            from_cache=True,
        )

    def cached_archive(
        self, release: OptiScalerRelease
    ) -> CachedOptiScalerArchive | None:
        """Read and revalidate a cached archive without using the network."""

        _validate_release(release)
        archive_path, record_path = self._archive_paths(release)
        return self._cached_archive(release, archive_path, record_path)

    def ensure_archive(
        self, release: OptiScalerRelease
    ) -> CachedOptiScalerArchive:
        """Return a verified cached archive, downloading it at most once."""

        _validate_release(release)
        if release.asset.size > self.max_archive_bytes:
            raise OptiScalerDownloadError(
                "OptiScaler release archive exceeds the download size limit"
            )
        archive_path, record_path = self._archive_paths(release)
        cached = self._cached_archive(release, archive_path, record_path)
        if cached is not None:
            return cached
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        request = Request(
            release.asset.download_url,
            headers={
                "Accept": "application/octet-stream",
                "User-Agent": "Game-Optimization-Linux",
            },
            method="GET",
        )
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=archive_path.parent,
                prefix=f".{archive_path.stem}.",
                suffix=release.asset.suffix,
                delete=False,
            ) as output:
                temporary_path = Path(output.name)
                digest = sha256()
                downloaded = 0
                try:
                    with self._open(request) as response:
                        status = int(getattr(response, "status", 200))
                        if status != 200:
                            raise OptiScalerDownloadError(
                                f"OptiScaler archive download returned HTTP {status}"
                            )
                        final_url_getter = getattr(response, "geturl", None)
                        final_url = (
                            str(final_url_getter())
                            if callable(final_url_getter) else release.asset.download_url
                        )
                        parsed_final = urlparse(final_url)
                        final_host = (parsed_final.hostname or "").casefold()
                        if (
                            parsed_final.scheme != "https"
                            or not (
                                final_host == "github.com"
                                or final_host == "release-assets.githubusercontent.com"
                                or final_host == "objects.githubusercontent.com"
                                or final_host.endswith(".githubusercontent.com")
                            )
                        ):
                            raise OptiScalerDownloadError(
                                "OptiScaler archive redirected outside official GitHub asset hosting"
                            )
                        while True:
                            chunk = response.read(DOWNLOAD_CHUNK_SIZE)
                            if not chunk:
                                break
                            downloaded += len(chunk)
                            if downloaded > self.max_archive_bytes:
                                raise OptiScalerDownloadError(
                                    "OptiScaler archive exceeds the download size limit"
                                )
                            output.write(chunk)
                            digest.update(chunk)
                except HTTPError as error:
                    raise OptiScalerDownloadError(
                        f"OptiScaler archive download returned HTTP {error.code}"
                    ) from error
                except (URLError, TimeoutError, ConnectionError, OSError) as error:
                    raise OptiScalerDownloadError(
                        f"could not download the official OptiScaler archive: {error}"
                    ) from error
                output.flush()
                os.fsync(output.fileno())
            if downloaded != release.asset.size:
                raise OptiScalerDownloadError(
                    "downloaded OptiScaler archive size does not match GitHub metadata"
                )
            self._validate_archive_payload(temporary_path)
            archive_hash = digest.hexdigest()
            if release.asset.digest:
                expected_digest = release.asset.digest.partition(":")[2].casefold()
                if archive_hash != expected_digest:
                    raise OptiScalerDownloadError(
                        "downloaded OptiScaler archive does not match the GitHub SHA-256 digest"
                    )
            os.replace(temporary_path, archive_path)
            temporary_path = None
            _atomic_json_write(
                record_path,
                {
                    "schema_version": ARCHIVE_CACHE_SCHEMA_VERSION,
                    "repository": OFFICIAL_REPOSITORY,
                    "tag_name": release.tag_name,
                    "version": release.version,
                    "asset_name": release.asset.name,
                    "download_url": release.asset.download_url,
                    "size": downloaded,
                    "sha256": archive_hash,
                    "github_digest": release.asset.digest,
                    "downloaded_at": self._clock(),
                },
            )
            return CachedOptiScalerArchive(
                path=archive_path,
                sha256=archive_hash,
                size=downloaded,
                release=release,
                from_cache=False,
            )
        except OSError as error:
            raise OptiScalerCacheError(
                f"could not store the OptiScaler release archive: {error}"
            ) from error
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass


__all__ = [
    "ARCHIVE_CACHE_SCHEMA_VERSION",
    "CachedOptiScalerArchive",
    "DEFAULT_MAX_ARCHIVE_BYTES",
    "DEFAULT_METADATA_MAX_AGE_SECONDS",
    "METADATA_CACHE_SCHEMA_VERSION",
    "OFFICIAL_RELEASES_URL",
    "OFFICIAL_REPOSITORY",
    "OptiScalerCacheError",
    "OptiScalerDownloadError",
    "OptiScalerMetadataError",
    "OptiScalerNetworkError",
    "OptiScalerOnlineError",
    "OptiScalerRelease",
    "OptiScalerReleaseAsset",
    "OptiScalerReleaseClient",
    "SUPPORTED_ARCHIVE_SUFFIXES",
    "parse_latest_stable_release",
]
