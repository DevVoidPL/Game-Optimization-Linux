"""FSR 4 model/DLL variants layered on a managed OptiScaler installation.

Two independent, mutually exclusive additions are handled here:

* ``fsr4_driver`` - AMD's driver FSR 4.1.2 libraries (``amdvkffx64.dll`` and
  ``amdxcffx64.dll``).  OptiScaler v10 loads them from its own folder; the
  INT8 Lite model (``ffxProvider_FSR4Lite_Int8``) exists only there.
* ``fsr_upscaler_variant`` - Cyboman's rewritten FSR 4.1.1 INT8 upscaler DLL
  that replaces OptiScaler's bundled ``amd_fidelityfx_upscaler_dx12.dll``
  (official 4.1.1.2740).  It is never combined with INT8 Lite (4.1.2).

Every artifact is pinned by URL, size and SHA-256.  Files are written through
the OptiScaler manifest so verification, removal and rollback keep working.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
import json
import logging
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..models import Game
from .archive_reader import ArchiveReadError, open_archive
from .mangohud import _atomic_write
from .optiscaler import OptiScalerError, OptiScalerService

logger = logging.getLogger(__name__)

UPSCALER_DLL = "amd_fidelityfx_upscaler_dx12.dll"
# OptiScaler 0.9.4 and v10.0.0-pre1 both bundle this AMD-signed 4.1.1.2740.
OFFICIAL_UPSCALER_SHA256 = (
    "d0dcccc74a43c44ba435b7a369b456e0970d8a4464e4bd683119b374f2c9fb46"
)
DRIVER_COMPONENT = "fsr4_driver"
VARIANT_COMPONENT = "fsr_upscaler_variant"


@dataclass(frozen=True, slots=True)
class PinnedArchive:
    key: str
    url: str
    size: int
    sha256: str
    # Nested archive inside the downloaded one (name, sha256), if any.
    inner_name: str = ""
    inner_sha256: str = ""


@dataclass(frozen=True, slots=True)
class PinnedFile:
    name: str
    sha256: str
    size: int


@dataclass(frozen=True, slots=True)
class FsrArtifact:
    key: str
    label: str
    component: str
    archive: PinnedArchive
    files: tuple[PinnedFile, ...]
    fsr_version: str
    model: str
    precision: str
    source: str
    source_url: str
    notes: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "component": self.component,
            "fsrVersion": self.fsr_version,
            "model": self.model,
            "precision": self.precision,
            "source": self.source,
            "sourceUrl": self.source_url,
            "notes": self.notes,
            "files": [item.name for item in self.files],
        }


_CYBOMAN_ARCHIVE = PinnedArchive(
    key="cyboman-2026-10-19",
    url=(
        "https://github.com/zgauthier2000/fsr4-shader-performance-overrides/"
        "releases/download/dll-2026-10-19/fsr4.1.1-cyboman-2026-10-19.7z"
    ),
    size=5629022,
    sha256="1360d9f02a082255ede8d540d95a296d85d356da38a61f0bdf0910bb1846ea33",
)
_DECKY_ARCHIVE = PinnedArchive(
    key="decky-optiscaler-v0.0.9",
    url=(
        "https://github.com/n1ckmp3/decky-optiscaler/releases/download/"
        "v0.0.9/Decky-OptiScaler-v0.0.9.zip"
    ),
    size=108050767,
    sha256="e10ceea1813919efe3693420c5025c91c58b4cd0f523e812d489edd40dc7cbac",
    inner_name="OptiScaler-10.0.0-pre1.7z",
    inner_sha256="f75ece86c1235cbbec65093262ca998dc107dda8efccfef1270ebe6f44c22e6e",
)
_CYBOMAN_URL = (
    "https://github.com/zgauthier2000/fsr4-shader-performance-overrides/"
    "releases/tag/dll-2026-10-19"
)
_CYBOMAN_SOURCE = (
    "zgauthier2000/fsr4-shader-performance-overrides dll-2026-10-19 (desktop)"
)

FSR_ARTIFACTS: dict[str, FsrArtifact] = {
    "fsr4_driver_4_1_2": FsrArtifact(
        key="fsr4_driver_4_1_2",
        label="AMD driver FSR 4.1.2 (FSR 4 INT8 Lite)",
        component=DRIVER_COMPONENT,
        archive=_DECKY_ARCHIVE,
        files=(
            PinnedFile(
                "amdvkffx64.dll",
                "80e761f7c8c0ec8ead7b321df01911baaf917defae51388d29eaecb1d6bc58bd",
                72497568,
            ),
            PinnedFile(
                "amdxcffx64.dll",
                "c33a287f3048bf02311cae430b6a977500b94ca7b315cd7bff751e6b03e483e3",
                85684104,
            ),
        ),
        fsr_version="4.1.2",
        model="INT8 Lite",
        precision="INT8",
        source=(
            "AMD-signed driver files (FFX 2.4.0.4370) redistributed in "
            "Decky OptiScaler v0.0.9"
        ),
        source_url="https://github.com/n1ckmp3/decky-optiscaler/releases/tag/v0.0.9",
        notes="Requires OptiScaler v10 (Fsr4GpuSupport/Fsr4Model).",
    ),
    "cyboman_exact": FsrArtifact(
        key="cyboman_exact",
        label="FSR 4.1.1 INT8 - Cyboman Exact (dll-2026-10-19)",
        component=VARIANT_COMPONENT,
        archive=_CYBOMAN_ARCHIVE,
        files=(
            PinnedFile(
                UPSCALER_DLL,
                "8f8bcd877e3364181cb14765027603dd19ae10c834336247b66eeaf06a7a9d86",
                38898176,
            ),
        ),
        fsr_version="4.1.1",
        model="INT8 (full model, rewritten shaders)",
        precision="INT8",
        source=_CYBOMAN_SOURCE,
        source_url=_CYBOMAN_URL,
        notes="Same picture as AMD's 4.1.1 DLL, faster shaders. RX 6000/7000 only.",
    ),
    "cyboman_lossy2": FsrArtifact(
        key="cyboman_lossy2",
        label="FSR 4.1.1 INT8 - Cyboman Lossy2 (dll-2026-10-19)",
        component=VARIANT_COMPONENT,
        archive=_CYBOMAN_ARCHIVE,
        files=(
            PinnedFile(
                UPSCALER_DLL,
                "4143c766b1062f5d8fd9bc3442976939865115455788a2097b358fd6bb4313ff",
                52675072,
            ),
        ),
        fsr_version="4.1.1",
        model="INT8 (network on alternate frames, trained weights)",
        precision="INT8",
        source=_CYBOMAN_SOURCE,
        source_url=_CYBOMAN_URL,
        notes=(
            "Changes the picture: the network runs on every other frame "
            "(two in three in Ultra Performance). RX 6000/7000 only."
        ),
    ),
}
VARIANT_ARTIFACT_KEYS = ("cyboman_exact", "cyboman_lossy2")
DRIVER_ARTIFACT_KEY = "fsr4_driver_4_1_2"
# v10 loads driver FSR libraries from OptiScaler's own folder (Decky layout).
DRIVER_DIRECTORY = "OptiScaler"


class FsrVariantError(OptiScalerError):
    pass


def gpu_variant_compatibility(gpu: str) -> str:
    """Cyboman desktop DLLs: RX 6000/7000 only; never RX 9000 (upstream)."""

    text = str(gpu or "").casefold()
    if re.search(r"\brx\s*9\d{3}\b", text) or "rdna4" in text:
        return "unsupported"
    if re.search(r"\brx\s*[67]\d{3}", text) or any(
        marker in text for marker in ("rdna2", "rdna3", "navi 2", "navi 3")
    ):
        return "supported"
    return "unknown"


def _hash_file(path: Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class FsrArtifactClient:
    """Download pinned archives once and extract verified files to a cache."""

    def __init__(
        self,
        cache_root: Path,
        *,
        opener: Callable[..., Any] | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.cache_root = Path(cache_root)
        self.opener = opener or urlopen
        self.timeout = timeout

    def files_dir(self, artifact: FsrArtifact) -> Path:
        return self.cache_root / "files" / artifact.key

    def cached_files(self, artifact: FsrArtifact) -> dict[str, Path] | None:
        """Return verified cached files, or ``None`` when any is missing."""

        result: dict[str, Path] = {}
        for item in artifact.files:
            path = self.files_dir(artifact) / item.name
            if (
                not path.is_file()
                or path.is_symlink()
                or path.stat().st_size != item.size
                or _hash_file(path) != item.sha256
            ):
                return None
            result[item.name] = path
        return result

    def _download(self, archive: PinnedArchive) -> Path:
        directory = self.cache_root / "archives"
        directory.mkdir(parents=True, exist_ok=True)
        name = PurePosixPath(archive.url).name
        target = directory / name
        if (
            target.is_file()
            and target.stat().st_size == archive.size
            and _hash_file(target) == archive.sha256
        ):
            return target
        request = Request(
            archive.url,
            headers={
                "Accept": "application/octet-stream",
                "User-Agent": "Game-Optimization-Linux",
            },
        )
        with tempfile.NamedTemporaryFile(
            dir=directory, prefix=f".{name}.", delete=False
        ) as output:
            temporary = Path(output.name)
        try:
            digest = sha256()
            written = 0
            with self.opener(request, timeout=self.timeout) as response, temporary.open(
                "wb"
            ) as output:
                if int(getattr(response, "status", 200)) != 200:
                    raise FsrVariantError(
                        f"FSR artifact download returned HTTP {response.status}"
                    )
                while chunk := response.read(1024 * 1024):
                    written += len(chunk)
                    if written > archive.size:
                        raise FsrVariantError("FSR artifact is larger than the pinned size")
                    output.write(chunk)
                    digest.update(chunk)
            if written != archive.size:
                raise FsrVariantError("FSR artifact size does not match the pinned size")
            if digest.hexdigest() != archive.sha256:
                raise FsrVariantError("FSR artifact checksum does not match the pinned SHA-256")
            temporary.replace(target)
            return target
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            raise FsrVariantError(f"could not download the FSR artifact: {error}") from error
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _extract(archive_path: Path, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=False)
        try:
            open_archive(archive_path).extract_to(destination)
        except ArchiveReadError as error:
            raise FsrVariantError(f"FSR artifact archive is invalid: {error}") from error


    def ensure(
        self, artifact: FsrArtifact, progress: Callable[[str, float], None] | None = None
    ) -> dict[str, Path]:
        emit = progress or (lambda _stage, _value: None)
        cached = self.cached_files(artifact)
        if cached is not None:
            return cached
        emit("Downloading FSR files", 0.05)
        archive_path = self._download(artifact.archive)
        emit("Verifying FSR files", 0.45)
        work_root = self.cache_root / "work"
        work_root.mkdir(parents=True, exist_ok=True)
        wanted = {item.sha256: item for item in artifact.files}
        with tempfile.TemporaryDirectory(prefix="extract-", dir=work_root) as raw:
            work = Path(raw)
            search_root = work / "outer"
            self._extract(archive_path, search_root)
            if artifact.archive.inner_name:
                inner = [
                    path for path in search_root.rglob(artifact.archive.inner_name)
                    if path.is_file() and not path.is_symlink()
                ]
                if len(inner) != 1 or _hash_file(inner[0]) != artifact.archive.inner_sha256:
                    raise FsrVariantError("nested FSR archive checksum does not match")
                search_root = work / "inner"
                self._extract(inner[0], search_root)
            found: dict[str, Path] = {}
            names = {item.name.casefold() for item in artifact.files}
            for path in search_root.rglob("*"):
                if (
                    path.name.casefold() in names
                    and path.is_file()
                    and not path.is_symlink()
                ):
                    digest = _hash_file(path)
                    if digest in wanted:
                        found[digest] = path
            final = self.files_dir(artifact)
            staging = final.with_name(final.name + ".partial")
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True)
            try:
                for item in artifact.files:
                    source = found.get(item.sha256)
                    if source is None:
                        raise FsrVariantError(
                            f"pinned file {item.name} is missing from the FSR artifact"
                        )
                    shutil.copyfile(source, staging / item.name)
                shutil.rmtree(final, ignore_errors=True)
                staging.replace(final)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
        verified = self.cached_files(artifact)
        if verified is None:
            raise FsrVariantError("cached FSR files failed verification")
        return verified


class FsrVariantService:
    """Apply/restore FSR artifacts inside one managed OptiScaler installation.

    Upscaler variants replace the managed official
    ``amd_fidelityfx_upscaler_dx12.dll``; driver files are added to the
    OptiScaler folder.  The manifest's ``after_sha256`` values follow every
    change so OptiScaler verification and removal stay exact, and the original
    bytes are kept under the installation backup for restore.
    """

    COMPONENT_KEY = "fsr_variants"

    def __init__(
        self, optiscaler: OptiScalerService, client: FsrArtifactClient
    ) -> None:
        self.optiscaler = optiscaler
        self.client = client

    # -- manifest helpers -------------------------------------------------
    def _context(self, game: Game) -> tuple[Any, dict[str, Any], Path, Path]:
        service = self.optiscaler
        profile = service.profile_repository.load(service.game_key(game))
        if (
            not profile.enabled
            or profile.installation_state != "installed"
            or not profile.manifest_id
        ):
            raise FsrVariantError("OptiScaler must be installed before changing the FSR variant")
        manifest = service._load_manifest(profile)
        root = service._install_root_for_change(game, manifest)
        return profile, manifest, root, service.manifest_path(profile.app_id, profile.manifest_id)

    @classmethod
    def components(cls, manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        raw = manifest.get("components", {})
        group = raw.get(cls.COMPONENT_KEY, {}) if isinstance(raw, Mapping) else {}
        if not isinstance(group, Mapping):
            return {}
        return {
            str(name): dict(value)
            for name, value in group.items()
            if isinstance(value, Mapping)
        }

    @staticmethod
    def _upscaler_entry(manifest: Mapping[str, Any]) -> dict[str, Any] | None:
        entries = [
            item
            for item in manifest.get("installed_files", [])
            if isinstance(item, Mapping)
            and PurePosixPath(str(item.get("relative_path", ""))).name.casefold()
            == UPSCALER_DLL
        ]
        return dict(entries[0]) if len(entries) == 1 else None

    @staticmethod
    def _is_v10_layout(manifest: Mapping[str, Any]) -> bool:
        return any(
            isinstance(item, Mapping)
            and str(item.get("relative_path", "")).casefold().startswith(
                DRIVER_DIRECTORY.casefold() + "/"
            )
            for item in manifest.get("installed_files", [])
        )

    def _backup_dir(self, profile: Any, component: str) -> Path:
        return (
            self.optiscaler.backup_root(profile.app_id, profile.manifest_id)
            / ".fsr-variants"
            / component
        )

    @staticmethod
    def _set_after_hash(manifest: dict[str, Any], relative: str, digest: str) -> None:
        for name in ("installed_files", "created_files", "replaced_files"):
            for item in manifest.get(name, []):
                if isinstance(item, dict) and str(item.get("relative_path", "")) == relative:
                    item["after_sha256"] = digest



    # -- transactional file change ---------------------------------------
    def _transaction(
        self,
        game: Game,
        changes: list[tuple[str, Path | None, str]],
        mutate_manifest: Callable[[dict[str, Any]], None],
    ) -> None:
        """Write ``(relative, source or None=delete, expected_hash)`` atomically.

        On any failure every touched file and the manifest are restored to
        their exact previous bytes.
        """

        service = self.optiscaler
        service._assert_mutation_allowed(game)
        profile, manifest, root, manifest_path = self._context(game)
        manifest_before = manifest_path.read_bytes()
        work = service.backup_root(profile.app_id, profile.manifest_id)
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".fsr-rollback-", dir=work) as raw:
            rollback = Path(raw)
            snapshots: list[tuple[Path, Path | None]] = []
            try:
                for index, (relative, source, expected) in enumerate(changes):
                    target = service._target(root, relative)
                    if target.is_symlink() or (target.exists() and not target.is_file()):
                        raise FsrVariantError(f"managed path is not a regular file: {relative}")
                    snapshot: Path | None = None
                    if target.is_file():
                        snapshot = rollback / f"{index}.bin"
                        shutil.copy2(target, snapshot)
                    snapshots.append((target, snapshot))
                    if source is None:
                        target.unlink(missing_ok=True)
                        continue
                    service._copy_atomic(source, target)
                    if service._hash_file(target) != expected:
                        raise FsrVariantError(f"installed file hash mismatch: {relative}")
                updated = json.loads(manifest_before.decode("utf-8"))
                mutate_manifest(updated)
                updated["fsr_variants_updated_at"] = datetime.now(UTC).isoformat()
                _atomic_write(
                    manifest_path,
                    json.dumps(updated, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                )
            except Exception:
                for target, snapshot in reversed(snapshots):
                    try:
                        if snapshot is not None:
                            service._copy_atomic(snapshot, target)
                        else:
                            target.unlink(missing_ok=True)
                    except OSError:
                        logger.exception("FSR variant rollback failed for %s", target)
                try:
                    manifest_path.write_bytes(manifest_before)
                except OSError:
                    logger.exception("FSR variant manifest rollback failed")
                raise


    # -- upscaler DLL variants (Cyboman) ---------------------------------
    def apply_variant(
        self,
        game: Game,
        key: str,
        *,
        gpu: str = "",
        progress: Callable[[str, float], None] | None = None,
    ) -> dict[str, Any]:
        artifact = FSR_ARTIFACTS.get(str(key))
        if artifact is None or artifact.key not in VARIANT_ARTIFACT_KEYS:
            raise FsrVariantError(f"unknown FSR variant: {key}")
        if gpu_variant_compatibility(gpu) == "unsupported":
            raise FsrVariantError(
                "Cyboman FSR 4.1.1 DLLs are not for Radeon RX 9000 (RDNA 4) GPUs"
            )
        profile, manifest, root, _path = self._context(game)
        components = self.components(manifest)
        if "driver" in components or profile.fsr4_mode == "lite":
            raise FsrVariantError(
                "FSR 4 INT8 Lite uses AMD's 4.1.2 driver model; Cyboman DLLs are "
                "built for FSR 4.1.1. Switch away from Lite first"
            )
        entry = self._upscaler_entry(manifest)
        if entry is None:
            raise FsrVariantError(
                "the managed OptiScaler payload has no amd_fidelityfx_upscaler_dx12.dll"
            )
        relative = str(entry.get("relative_path", ""))
        target = self.optiscaler._target(root, relative)
        current = self.optiscaler._hash_file(target) if target.is_file() else ""
        if current != str(entry.get("after_sha256", "")):
            raise FsrVariantError(
                "amd_fidelityfx_upscaler_dx12.dll was changed outside GameOpti; nothing was replaced"
            )
        existing = components.get("upscaler")
        backup_dir = self._backup_dir(profile, "upscaler")
        backup = backup_dir / UPSCALER_DLL
        if existing is None:
            if current != OFFICIAL_UPSCALER_SHA256:
                raise FsrVariantError(
                    "Cyboman DLLs require OptiScaler's official FSR 4.1.1.2740 upscaler; "
                    "the installed upscaler is a different build"
                )
            backup_dir.mkdir(parents=True, exist_ok=True)
            self.optiscaler._copy_atomic(target, backup)
            if self.optiscaler._hash_file(backup) != current:
                raise FsrVariantError("upscaler backup verification failed")
        elif existing.get("artifact") == artifact.key:
            return self.status(game)
        elif not backup.is_file() or self.optiscaler._hash_file(backup) != str(
            existing.get("originalSha256", "")
        ):
            raise FsrVariantError("the original upscaler backup is missing or corrupt")
        files = self.client.ensure(artifact, progress)
        pinned = artifact.files[0]
        record = {
            "artifact": artifact.key,
            "label": artifact.label,
            "fsrVersion": artifact.fsr_version,
            "relativePath": relative,
            "sha256": pinned.sha256,
            "originalSha256": (
                str(existing.get("originalSha256")) if existing else current
            ),
            "backupPath": str(backup),
            "sourceUrl": artifact.source_url,
            "installedAt": datetime.now(UTC).isoformat(),
        }

        def mutate(updated: dict[str, Any]) -> None:
            self._set_after_hash(updated, relative, pinned.sha256)
            self._store_component(updated, "upscaler", record)

        if progress:
            progress("Replacing the upscaler DLL", 0.8)
        self._transaction(game, [(relative, files[pinned.name], pinned.sha256)], mutate)
        self._remember_variant(game, artifact.key)
        return self.status(game)

    def _remember_variant(self, game: Game, variant: str) -> None:
        repository = self.optiscaler.profile_repository
        profile = repository.load(self.optiscaler.game_key(game))
        repository.save(
            replace(
                profile,
                fsr_variant=variant,
                runtime_verification_status="not_verified",
                updated_at=datetime.now(UTC),
            )
        )

    def restore_variant(self, game: Game) -> dict[str, Any]:
        profile, manifest, root, _path = self._context(game)
        existing = self.components(manifest).get("upscaler")
        if existing is None:
            return self.status(game)
        relative = str(existing.get("relativePath", ""))
        target = self.optiscaler._target(root, relative)
        current = self.optiscaler._hash_file(target) if target.is_file() else ""
        original = str(existing.get("originalSha256", ""))
        backup = self._backup_dir(profile, "upscaler") / UPSCALER_DLL
        if current not in {str(existing.get("sha256", "")), original}:
            raise FsrVariantError(
                "the community upscaler DLL was changed outside GameOpti; it was preserved"
            )
        if not backup.is_file() or self.optiscaler._hash_file(backup) != original:
            raise FsrVariantError("the original upscaler backup is missing or corrupt")

        def mutate(updated: dict[str, Any]) -> None:
            self._set_after_hash(updated, relative, original)
            self._store_component(updated, "upscaler", None)

        self._transaction(game, [(relative, backup, original)], mutate)
        self._remember_variant(game, "official")
        return self.status(game)

    @classmethod
    def _store_component(
        cls, manifest: dict[str, Any], name: str, record: Mapping[str, Any] | None
    ) -> None:
        components = dict(manifest.get("components", {}) or {})
        group = dict(components.get(cls.COMPONENT_KEY, {}) or {})
        if record is None:
            group.pop(name, None)
        else:
            group[name] = dict(record)
        if group:
            components[cls.COMPONENT_KEY] = group
        else:
            components.pop(cls.COMPONENT_KEY, None)
        manifest["components"] = components
        manifest["managed_components"] = sorted(components)


    # -- AMD driver FSR 4.1.2 files (INT8 Lite) ---------------------------
    def install_driver(
        self, game: Game, *, progress: Callable[[str, float], None] | None = None
    ) -> dict[str, Any]:
        artifact = FSR_ARTIFACTS[DRIVER_ARTIFACT_KEY]
        _profile, manifest, root, _path = self._context(game)
        if not self._is_v10_layout(manifest):
            raise FsrVariantError(
                "FSR 4 INT8 Lite needs OptiScaler v10 (Nightly channel); the installed "
                "release has no OptiScaler folder or Fsr4Model key"
            )
        components = self.components(manifest)
        if "upscaler" in components:
            raise FsrVariantError(
                "Restore the official FSR 4.1.1 upscaler before enabling INT8 Lite; "
                "Cyboman DLLs are not combined with AMD's 4.1.2 Lite model"
            )
        if "driver" in components:
            return self.status(game)
        managed = {
            str(item.get("relative_path", "")).casefold()
            for item in manifest.get("installed_files", [])
            if isinstance(item, Mapping)
        }
        for item in artifact.files:
            relative = f"{DRIVER_DIRECTORY}/{item.name}"
            target = self.optiscaler._target(root, relative)
            if relative.casefold() in managed or target.exists() or target.is_symlink():
                raise FsrVariantError(
                    f"{relative} already exists in the game folder; it was not replaced"
                )
        files = self.client.ensure(artifact, progress)
        changes: list[tuple[str, Path | None, str]] = [
            (f"{DRIVER_DIRECTORY}/{item.name}", files[item.name], item.sha256)
            for item in artifact.files
        ]
        record = {
            "artifact": artifact.key,
            "label": artifact.label,
            "fsrVersion": artifact.fsr_version,
            "files": [
                {"relativePath": relative, "sha256": digest}
                for relative, _source, digest in changes
            ],
            "sourceUrl": artifact.source_url,
            "installedAt": datetime.now(UTC).isoformat(),
        }

        def mutate(updated: dict[str, Any]) -> None:
            for pinned in artifact.files:
                relative = f"{DRIVER_DIRECTORY}/{pinned.name}"
                updated.setdefault("installed_files", []).append(
                    {
                        "relative_path": relative,
                        "archive_member": f"{artifact.key}/{pinned.name}",
                        "size": pinned.size,
                        "after_sha256": pinned.sha256,
                    }
                )
                updated.setdefault("created_files", []).append(
                    {"relative_path": relative, "after_sha256": pinned.sha256}
                )
            self._store_component(updated, "driver", record)

        if progress:
            progress("Installing AMD driver FSR files", 0.8)
        self._transaction(game, changes, mutate)
        return self.status(game)

    def remove_driver(self, game: Game) -> dict[str, Any]:
        _profile, manifest, root, _path = self._context(game)
        existing = self.components(manifest).get("driver")
        if existing is None:
            return self.status(game)
        expected = {
            str(item.get("relativePath", "")): str(item.get("sha256", ""))
            for item in existing.get("files", [])
        }
        for relative, digest in expected.items():
            target = self.optiscaler._target(root, relative)
            if target.is_file() and self.optiscaler._hash_file(target) != digest:
                raise FsrVariantError(
                    f"{relative} was changed outside GameOpti; it was preserved"
                )
        folded = {relative.casefold() for relative in expected}

        def mutate(updated: dict[str, Any]) -> None:
            for name in ("installed_files", "created_files"):
                updated[name] = [
                    item for item in updated.get(name, [])
                    if not (
                        isinstance(item, Mapping)
                        and str(item.get("relative_path", "")).casefold() in folded
                    )
                ]
            self._store_component(updated, "driver", None)

        self._transaction(game, [(relative, None, "") for relative in expected], mutate)
        return self.status(game)


    # -- status -----------------------------------------------------------
    def status(self, game: Game, *, gpu: str = "") -> dict[str, Any]:
        try:
            _profile, manifest, root, _path = self._context(game)
        except OptiScalerError as error:
            return {"available": False, "error": str(error), "variant": "official"}
        components = self.components(manifest)
        upscaler = components.get("upscaler")
        driver = components.get("driver")
        drift: list[str] = []
        tracked: list[tuple[str, str]] = []
        if upscaler:
            tracked.append(
                (str(upscaler.get("relativePath", "")), str(upscaler.get("sha256", "")))
            )
        if driver:
            tracked.extend(
                (str(item.get("relativePath", "")), str(item.get("sha256", "")))
                for item in driver.get("files", [])
            )
        for relative, digest in tracked:
            target = self.optiscaler._target(root, relative)
            if not target.is_file() or self.optiscaler._hash_file(target) != digest:
                drift.append(relative)
        entry = self._upscaler_entry(manifest)
        return {
            "available": True,
            "variant": str(upscaler.get("artifact")) if upscaler else "official",
            "variantLabel": str(upscaler.get("label")) if upscaler else "",
            "driverInstalled": bool(driver),
            "driverLabel": str(driver.get("label")) if driver else "",
            "v10Layout": self._is_v10_layout(manifest),
            "officialUpscaler": bool(
                entry and str(entry.get("after_sha256", "")) == OFFICIAL_UPSCALER_SHA256
            ),
            "gpuCompatibility": gpu_variant_compatibility(gpu),
            "drift": drift,
            "artifacts": [item.to_dict() for item in FSR_ARTIFACTS.values()],
        }


# Upstream log lines (OptiScaler v10 FfxApi_Proxy.h, FSR4Models.cpp,
# FSR4DriverProvider*.cpp).  A log is evidence of a session, not of the
# currently configured state, so the result carries the log timestamp.
_SR_VERSION_RE = re.compile(r"FfxApi (?:Dx12|Vk|Vulkan) SR version: (\d+)\.(\d+)\.(\d+)")
_LITE_HOOK_RE = re.compile(r"Hooked the support check of ffxProvider_FSR4Lite_Int8")
_DRIVER_LOADED_RE = re.compile(r"amd(?:vk|xc)ffx64\.dll loaded from", re.IGNORECASE)
_NO_FSR4_RE = re.compile(
    r"GPU doesn't support FSR 4|can't run any FSR 4 model|disabling FSR 4", re.IGNORECASE
)
_CYBOMAN_RE = re.compile(r"4\.1\.1-(?:igpu-)?(?:lossy2?-)?cyboman", re.IGNORECASE)
_MAX_LOG_BYTES = 8 * 1024 * 1024


def inspect_runtime_log(path: Path) -> dict[str, Any]:
    """Best-effort reading of OptiScaler.log for the model actually used."""

    result: dict[str, Any] = {
        "logFound": False,
        "status": "not_verified",
        "evidence": [],
        "logPath": str(path),
        "logModifiedAt": "",
    }
    try:
        if not path.is_file() or path.is_symlink():
            return result
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > _MAX_LOG_BYTES:
                handle.seek(size - _MAX_LOG_BYTES)
            text = handle.read().decode("utf-8", errors="replace")
        result["logModifiedAt"] = datetime.fromtimestamp(
            path.stat().st_mtime, UTC
        ).isoformat()
    except OSError:
        return result
    result["logFound"] = True
    evidence: list[str] = []
    versions = [tuple(int(x) for x in m.groups()) for m in _SR_VERSION_RE.finditer(text)]
    if versions:
        evidence.append("FFX SR version %d.%d.%d" % versions[-1])
    lite = bool(_LITE_HOOK_RE.search(text))
    driver = bool(_DRIVER_LOADED_RE.search(text))
    if lite:
        evidence.append("ffxProvider_FSR4Lite_Int8 hooked")
    if driver:
        evidence.append("AMD driver FSR library loaded")
    if _CYBOMAN_RE.search(text):
        evidence.append("Cyboman upscaler build reported")
    no_fsr4 = bool(_NO_FSR4_RE.search(text))
    if no_fsr4:
        evidence.append("OptiScaler reported that FSR 4 is unavailable")
    if no_fsr4 or (versions and versions[-1][0] < 4):
        result["status"] = "verified_fsr3_fallback"
    elif lite and driver:
        # The hook proves Lite was selectable; the overlay watermark is still
        # the only per-frame confirmation of the running model.
        result["status"] = "log_indicates_fsr4_int8_lite"
    elif versions and versions[-1][0] >= 4:
        result["status"] = "log_indicates_fsr4"
    result["evidence"] = evidence
    return result


__all__ = [
    "DRIVER_ARTIFACT_KEY",
    "FSR_ARTIFACTS",
    "FsrArtifact",
    "FsrArtifactClient",
    "FsrVariantError",
    "FsrVariantService",
    "OFFICIAL_UPSCALER_SHA256",
    "VARIANT_ARTIFACT_KEYS",
    "gpu_variant_compatibility",
    "inspect_runtime_log",
]
