"""Inventory and ownership rules for optional narrator components."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from typing import Any, Mapping
from urllib.parse import urlparse
from urllib.request import urlopen
import zipfile

from game_optimization_linux.config import NARRATOR_COMPONENTS_DIR
from game_optimization_linux.models.narrator import (
    NARRATOR_COMPONENT_SCHEMA_VERSION,
    NarratorComponent,
    NarratorComponentKind,
    NarratorComponentState,
)

from .narrator_persistence import _atomic_json_write


_COMPONENT_ID = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
_APPROVED_DOWNLOAD_HOSTS = frozenset(
    {
        "raw.githubusercontent.com",
        "data.argosopentech.com",
        "huggingface.co",

        # Hugging Face model storage / CDN endpoints.
        "cdn-lfs.huggingface.co",
        "cas-bridge.xethub.hf.co",
        "cas-server.xethub.hf.co",
        "cas-server.xethub-eu.hf.co",
        "transfer.xethub.hf.co",
        "transfer.xethub-eu.hf.co",
        "us.aws.cdn.hf.co",
        "us.gcp.cdn.hf.co",
        "cdn-lfs-us-1.hf.co",
        "cdn-lfs-eu-1.hf.co",
    }
)
_MAX_ARCHIVE_FILES = 4096
_MAX_ARCHIVE_SIZE = 512 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class NarratorComponentArtifact:
    source_url: str
    sha256: str
    size_bytes: int
    target_relative_path: str = ""
    archive_format: str = ""

    def __post_init__(self) -> None:
        parsed = urlparse(self.source_url)
        if parsed.scheme != "https" or parsed.hostname not in _APPROVED_DOWNLOAD_HOSTS:
            raise ValueError("component artifacts need an approved HTTPS URL")
        if len(self.sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.sha256.casefold()
        ):
            raise ValueError("component artifacts need a SHA-256 digest")
        if self.size_bytes <= 0:
            raise ValueError("component artifact size must be positive")
        if self.archive_format not in {"", "zip"}:
            raise ValueError("unsupported component archive format")
        if self.archive_format and self.target_relative_path:
            raise ValueError("archive artifacts are extracted into the component root")
        if not self.archive_format:
            relative = Path(self.target_relative_path)
            if (
                not self.target_relative_path
                or relative.is_absolute()
                or ".." in relative.parts
            ):
                raise ValueError("component artifact target path is invalid")


@dataclass(frozen=True, slots=True)
class NarratorPinnedFile:
    """A file of a component whose bytes are pinned before a download URL exists."""

    target_relative_path: str
    size_bytes: int
    sha256: str

    def __post_init__(self) -> None:
        relative = Path(self.target_relative_path)
        if not relative.parts or relative.is_absolute() or ".." in relative.parts:
            raise ValueError("pinned files need a safe relative target")
        if len(self.sha256) != 64 or any(c not in "0123456789abcdef" for c in self.sha256):
            raise ValueError("pinned files need a lowercase SHA-256 digest")
        if int(self.size_bytes) <= 0:
            raise ValueError("pinned files need a positive size")


@dataclass(frozen=True, slots=True)
class NarratorComponentDefinition:
    component_id: str
    kind: NarratorComponentKind
    name: str
    license_id: str
    runtime_license_id: str = ""
    artifact_license_id: str = ""
    version: str = ""
    download_size_bytes: int | None = None
    source_url: str = ""
    sha256: str = ""
    target_relative_path: str = ""
    attribution: str = ""
    install_ready: bool = False
    message: str = ""
    artifacts: tuple[NarratorComponentArtifact, ...] = ()
    # Former catalog entry: never offered for download; listed only while installed.
    legacy: bool = False
    # Exact files (size + SHA-256) accepted for this component; used when no
    # immutable download URL exists yet and for verified local installation.
    pinned_files: tuple[NarratorPinnedFile, ...] = ()

    def __post_init__(self) -> None:
        if not _COMPONENT_ID.fullmatch(self.component_id):
            raise ValueError("invalid narrator component id")
        object.__setattr__(self, "kind", NarratorComponentKind(self.kind))
        if self.install_ready:
            if not self.version.strip():
                raise ValueError("installable components need a version")
            if self.artifacts:
                if any((self.source_url, self.sha256, self.target_relative_path)):
                    raise ValueError("use either artifacts or the legacy single-file fields")
            elif not self.source_url or len(self.sha256) != 64 or not self.target_relative_path:
                raise ValueError("installable components need a pinned URL, SHA-256 and target")

    def resolved_artifacts(self) -> tuple[NarratorComponentArtifact, ...]:
        if self.artifacts:
            return self.artifacts
        if not self.source_url:
            return ()
        if self.download_size_bytes is None:
            raise ValueError("the single-file component needs a pinned download size")
        return (
            NarratorComponentArtifact(
                source_url=self.source_url,
                sha256=self.sha256,
                size_bytes=self.download_size_bytes,
                target_relative_path=self.target_relative_path,
            ),
        )


DEFAULT_NARRATOR_COMPONENTS = (
    NarratorComponentDefinition(
        component_id="capture.portal-pipewire",
        kind=NarratorComponentKind.CAPTURE,
        name="Wayland portal and PipeWire capture",
        license_id="application-runtime",
        message="The portal transport is provided by the application runtime",
    ),
    NarratorComponentDefinition(
        component_id="ocr.english-local",
        kind=NarratorComponentKind.OCR,
        name="Tesseract English subtitle OCR",
        license_id="Apache-2.0",
        runtime_license_id="Apache-2.0",
        artifact_license_id="Apache-2.0",
        version="tessdata_fast-8741641",
        download_size_bytes=4_113_088,
        source_url=(
            "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/"
            "87416418657359cb625c412a48b6e1d6d41c29bd/eng.traineddata"
        ),
        sha256="7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2",
        target_relative_path="tessdata/eng.traineddata",
        attribution="Tesseract OCR and tessdata_fast, Apache License 2.0",
        install_ready=True,
        message="Verified 3.9 MiB English tessdata_fast model",
    ),
    NarratorComponentDefinition(
        component_id="ocr.polish-local",
        kind=NarratorComponentKind.OCR,
        name="Tesseract Polish subtitle OCR",
        license_id="Apache-2.0",
        runtime_license_id="Apache-2.0",
        artifact_license_id="Apache-2.0",
        version="tessdata_fast-8741641",
        download_size_bytes=4_765_518,
        source_url=(
            "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/"
            "87416418657359cb625c412a48b6e1d6d41c29bd/pol.traineddata"
        ),
        sha256="c4476cdbc0e33d898d32345122b7be1cbf85ace15f920f06c7714756e1ef79b2",
        target_relative_path="tessdata/pol.traineddata",
        attribution="Tesseract OCR and tessdata_fast, Apache License 2.0",
        install_ready=True,
        message="Verified 4.5 MiB Polish tessdata_fast model",
    ),
    NarratorComponentDefinition(
        component_id="translation.opus-en-pl",
        kind=NarratorComponentKind.TRANSLATION,
        name="Argos OPUS English to Polish",
        license_id="CC-BY-4.0",
        runtime_license_id="MIT / Apache-2.0",
        artifact_license_id="CC-BY-4.0",
        version="1.9",
        download_size_bytes=67_294_886,
        attribution=(
            "OPUS-MT English to Polish model by Jörg Tiedemann and "
            "Santhosh Thottingal, CC BY 4.0"
        ),
        install_ready=True,
        message="Verified 64.2 MiB Argos OPUS model for local CPU translation",
        artifacts=(
            NarratorComponentArtifact(
                source_url=(
                    "https://data.argosopentech.com/argospm/v1/"
                    "translate-en_pl-1_9.argosmodel"
                ),
                sha256="85d865369326b6d8220876fbd7bc552fa5ec8b99e81161fab4a26f78187cedbc",
                size_bytes=67_294_886,
                archive_format="zip",
            ),
        ),
    ),
    NarratorComponentDefinition(
        component_id="tts.voidek",
        kind=NarratorComponentKind.TTS,
        name="Piper Polish voice - Voidek",
        license_id="LicenseRef-Voidek-Model",
        runtime_license_id="GPL-3.0-or-later",
        artifact_license_id="LicenseRef-Voidek-Model",
        version="e991-ca2ad0a",
        download_size_bytes=63_521_083,
        attribution="Voidek, a Polish Piper voice created by DevVoidPL",
        # Shipped inside the application packages and installed from there;
        # never downloaded at runtime.
        install_ready=False,
        message="The Voidek voice is included with GameOpti",
        pinned_files=(
            NarratorPinnedFile(
                "voices/voidek-pl-e991.onnx",
                63_516_050,
                "ca2ad0a22273e3850ce3def4d098f1993c2393c71584f474636afd08ab6af53b",
            ),
            NarratorPinnedFile(
                "voices/voidek-pl-e991.onnx.json",
                5_033,
                "4b14423d0575b35bc4ace4dc83d639e9f8d0fa8eab3f238bd9597bfab50af5db",
            ),
        ),
    ),
    NarratorComponentDefinition(
        component_id="tts.polish-voice",
        kind=NarratorComponentKind.TTS,
        legacy=True,
        name="Piper Polish voice - Gosia",
        license_id="MIT / CC0 source data",
        runtime_license_id="GPL-3.0-or-later",
        artifact_license_id="MIT; source dataset CC0",
        version="piper-voices-058271f",
        download_size_bytes=63_208_214,
        attribution="pl_PL-gosia-medium from rhasspy/piper-voices",
        install_ready=True,
        message="Verified 60.3 MiB Polish Piper voice for local CPU speech",
        artifacts=(
            NarratorComponentArtifact(
                source_url=(
                    "https://huggingface.co/rhasspy/piper-voices/resolve/"
                    "058271fb41b630e96989367e15b4514992a25b42/pl/pl_PL/"
                    "gosia/medium/pl_PL-gosia-medium.onnx"
                ),
                sha256="38f66464240ed74f186e6b7dc13c6e3b22e023426299f25c2b3cc9dfa9373fbc",
                size_bytes=63_201_294,
                target_relative_path="voices/pl_PL-gosia-medium.onnx",
            ),
            NarratorComponentArtifact(
                source_url=(
                    "https://huggingface.co/rhasspy/piper-voices/resolve/"
                    "058271fb41b630e96989367e15b4514992a25b42/pl/pl_PL/"
                    "gosia/medium/pl_PL-gosia-medium.onnx.json"
                ),
                sha256="956cd5b2a08dca5e780ad584a6d2e971ba3bd7fcd06297dfa6cd85c9fbcd3d42",
                size_bytes=6_920,
                target_relative_path="voices/pl_PL-gosia-medium.onnx.json",
            ),
        ),
    ),
    NarratorComponentDefinition(
        component_id="tts.polish-bass",
        kind=NarratorComponentKind.TTS,
        legacy=True,
        name="Piper Polish voice - Bass",
        license_id="MIT",
        runtime_license_id="GPL-3.0-or-later",
        artifact_license_id="Apache-2.0",
        version="piper-voices-5b44ec7",
        download_size_bytes=114_208_978,
        attribution="pl_PL-bass-high from rhasspy/piper-voices",
        install_ready=True,
        message="Verified 108.9 MiB Polish male Piper voice for local CPU speech",
        artifacts=(
            NarratorComponentArtifact(
                source_url=(
                    "https://huggingface.co/rhasspy/piper-voices/resolve/"
                    "5b44ec7bab7c5822cfec48fbd5aa99db71a823d6/pl/pl_PL/"
                    "bass/high/pl_PL-bass-high.onnx"
                ),
                sha256="73b8408967c58118700f21eb2413cd8b666c7844c6136cf674bf5dd56bde72c2",
                size_bytes=114_204_024,
                target_relative_path="voices/pl_PL-bass-high.onnx",
            ),
            NarratorComponentArtifact(
                source_url=(
                    "https://huggingface.co/rhasspy/piper-voices/resolve/"
                    "5b44ec7bab7c5822cfec48fbd5aa99db71a823d6/pl/pl_PL/"
                    "bass/high/pl_PL-bass-high.onnx.json"
                ),
                sha256="7bb41aa14fee87a31cc32264119c09e3553335196d3db15b39b1c18790e13c59",
                size_bytes=4_954,
                target_relative_path="voices/pl_PL-bass-high.onnx.json",
            ),
        ),
    ),
    NarratorComponentDefinition(
        component_id="tts.polish-darkman",
        kind=NarratorComponentKind.TTS,
        legacy=True,
        name="Piper Polish voice - Darkman",
        license_id="MIT / CC0 source data",
        runtime_license_id="GPL-3.0-or-later",
        artifact_license_id="MIT; source dataset CC0",
        version="piper-voices-c10ece1",
        download_size_bytes=63_206_110,
        attribution="pl_PL-darkman-medium from rhasspy/piper-voices",
        install_ready=True,
        message="Verified 60.3 MiB Polish male Piper voice for local CPU speech",
        artifacts=(
            NarratorComponentArtifact(
                source_url=(
                    "https://huggingface.co/rhasspy/piper-voices/resolve/"
                    "c10ece1aade47bb51c153c893d14e5bf8e5b7117/pl/pl_PL/"
                    "darkman/medium/pl_PL-darkman-medium.onnx"
                ),
                sha256="db505438a5364e8e2e0242c4324130a873ed660dfbe8d9689cef428ffb1b645f",
                size_bytes=63_201_294,
                target_relative_path="voices/pl_PL-darkman-medium.onnx",
            ),
            NarratorComponentArtifact(
                source_url=(
                    "https://huggingface.co/rhasspy/piper-voices/resolve/"
                    "c10ece1aade47bb51c153c893d14e5bf8e5b7117/pl/pl_PL/"
                    "darkman/medium/pl_PL-darkman-medium.onnx.json"
                ),
                sha256="70f999f11fa8ad13d3ef779041ee93c9f38be5abdbacdfad42449712fe91c81b",
                size_bytes=4_816,
                target_relative_path="voices/pl_PL-darkman-medium.onnx.json",
            ),
        ),
    ),
    NarratorComponentDefinition(
        component_id="audio.qt-pcm",
        kind=NarratorComponentKind.AUDIO,
        name="Qt PCM audio output",
        license_id="application-runtime",
        message="PCM is sent directly to the sandbox audio service",
    ),
)


class NarratorComponentManager:
    def __init__(
        self,
        root: Path = NARRATOR_COMPONENTS_DIR,
        *,
        definitions: tuple[NarratorComponentDefinition, ...] = DEFAULT_NARRATOR_COMPONENTS,
    ) -> None:
        self.root = Path(root)
        self._definitions = {item.component_id: item for item in definitions}
        self._runtime_states: dict[str, tuple[bool, str]] = {}

    def set_runtime_state(
        self, component_id: str, available: bool, message: str = ""
    ) -> None:
        if component_id not in self._definitions:
            raise KeyError(component_id)
        self._runtime_states[component_id] = (bool(available), str(message))

    def list_components(self) -> tuple[NarratorComponent, ...]:
        """Catalog components. Former catalog voices are never listed; their files
        stay on disk until a separate explicit cleanup action removes them."""
        return tuple(
            self.status(component_id)
            for component_id, definition in self._definitions.items()
            if not definition.legacy
        )

    def installed_legacy_components(self) -> tuple[str, ...]:
        """Former catalog voices still present on disk (for a future explicit cleanup)."""
        return tuple(
            component_id
            for component_id, definition in self._definitions.items()
            if definition.legacy and self._read_manifest(component_id) is not None
        )

    def status(self, component_id: str) -> NarratorComponent:
        definition = self._definition(component_id)
        runtime = self._runtime_states.get(component_id)
        manifest = self._read_manifest(component_id)
        update_available = bool(
            manifest is not None
            and definition.install_ready
            and definition.version
            and str(manifest.get("version", "")) != definition.version
        )
        if runtime is not None:
            available, runtime_message = runtime
            available = bool(
                available and (not definition.install_ready or manifest is not None)
            )
            return NarratorComponent(
                component_id=component_id,
                kind=definition.kind,
                name=definition.name,
                state=(
                    NarratorComponentState.UPDATE_AVAILABLE
                    if update_available
                    else (
                        NarratorComponentState.AVAILABLE
                        if available
                        else (
                            NarratorComponentState.ERROR
                            if manifest is not None
                            else (
                                NarratorComponentState.UNSUPPORTED
                                if definition.kind
                                in {
                                    NarratorComponentKind.CAPTURE,
                                    NarratorComponentKind.AUDIO,
                                }
                                else NarratorComponentState.NOT_INSTALLED
                            )
                        )
                    )
                ),
                version=str(manifest.get("version", "")) if manifest else "",
                installed_size_bytes=(
                    self._directory_size(self._component_path(component_id))
                    if manifest
                    else None
                ),
                license_id=(
                    str(manifest.get("license_id") or definition.license_id)
                    if manifest
                    else definition.license_id
                ),
                runtime_license_id=definition.runtime_license_id,
                artifact_license_id=definition.artifact_license_id,
                attribution=definition.attribution,
                download_size_bytes=definition.download_size_bytes,
                message=runtime_message or definition.message,
                managed=manifest is not None,
                update_version=definition.version if update_available else "",
            )
        if manifest is None:
            return NarratorComponent(
                component_id=component_id,
                kind=definition.kind,
                name=definition.name,
                state=NarratorComponentState.NOT_INSTALLED,
                license_id=definition.license_id,
                runtime_license_id=definition.runtime_license_id,
                artifact_license_id=definition.artifact_license_id,
                attribution=definition.attribution,
                download_size_bytes=definition.download_size_bytes,
                message=definition.message,
            )
        return NarratorComponent(
            component_id=component_id,
            kind=definition.kind,
            name=definition.name,
            state=(
                NarratorComponentState.UPDATE_AVAILABLE
                if update_available
                else NarratorComponentState.AVAILABLE
            ),
            version=str(manifest.get("version", "")),
            installed_size_bytes=self._directory_size(self._component_path(component_id)),
            download_size_bytes=definition.download_size_bytes,
            license_id=str(manifest.get("license_id") or definition.license_id),
            runtime_license_id=str(
                manifest.get("runtime_license_id") or definition.runtime_license_id
            ),
            artifact_license_id=str(
                manifest.get("artifact_license_id") or definition.artifact_license_id
            ),
            attribution=str(manifest.get("attribution") or definition.attribution),
            message=definition.message,
            managed=True,
            update_version=definition.version if update_available else "",
        )

    def can_install(self, component_id: str) -> bool:
        definition = self._definition(component_id)
        return definition.install_ready and not definition.legacy

    def is_legacy(self, component_id: str) -> bool:
        return self._definition(component_id).legacy

    def offer_message(self, component_id: str) -> str:
        """Why a component cannot be downloaded in this build."""
        definition = self._definition(component_id)
        if definition.legacy:
            return "This voice is no longer offered"
        return definition.message or "This component has no verified download source in the current build"

    def install(self, component_id: str) -> None:
        definition = self._definition(component_id)
        if definition.legacy:
            raise RuntimeError("This voice is no longer offered for download")
        if not definition.install_ready:
            raise RuntimeError(
                "This component has no verified download source in the current build"
            )
        self.root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{component_id}-", dir=self.root))
        backup = self.root / f".{component_id}.previous"
        target = self._component_path(component_id)
        try:
            installed_files: list[dict[str, Any]] = []
            for index, artifact in enumerate(definition.resolved_artifacts()):
                downloaded = staging / f".artifact-{index}"
                self._download_artifact(artifact, downloaded)
                if artifact.archive_format == "zip":
                    installed_files.extend(self._extract_zip(downloaded, staging))
                    downloaded.unlink()
                else:
                    relative = Path(artifact.target_relative_path)
                    destination = staging / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(downloaded, destination)
                    os.chmod(destination, 0o644)
                    installed_files.append(
                        {
                            "path": relative.as_posix(),
                            "size": artifact.size_bytes,
                            "sha256": artifact.sha256,
                        }
                    )
            if not installed_files:
                raise RuntimeError("The narrator component contains no installable files")
            _atomic_json_write(
                staging / "component.json",
                {
                    "schema_version": NARRATOR_COMPONENT_SCHEMA_VERSION,
                    "managed_by": "game-optimization-linux",
                    "component_id": component_id,
                    "kind": definition.kind.value,
                    "version": definition.version,
                    "license_id": definition.license_id,
                    "runtime_license_id": definition.runtime_license_id,
                    "artifact_license_id": definition.artifact_license_id,
                    "source_url": definition.source_url,
                    "sha256": definition.sha256,
                    "attribution": definition.attribution,
                    "artifacts": [
                        {
                            "source_url": artifact.source_url,
                            "size": artifact.size_bytes,
                            "sha256": artifact.sha256,
                            "archive_format": artifact.archive_format,
                        }
                        for artifact in definition.resolved_artifacts()
                    ],
                    "files": installed_files,
                },
            )
            if backup.exists():
                shutil.rmtree(backup)
            if target.exists():
                os.replace(target, backup)
            try:
                os.replace(staging, target)
            except Exception:
                if backup.exists() and not target.exists():
                    os.replace(backup, target)
                raise
            if backup.exists():
                shutil.rmtree(backup)
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    @staticmethod
    def _download_artifact(
        artifact: NarratorComponentArtifact, destination: Path
    ) -> None:
        parsed = urlparse(artifact.source_url)
        if parsed.scheme != "https" or parsed.hostname not in _APPROVED_DOWNLOAD_HOSTS:
            raise RuntimeError("The component source is not an approved HTTPS host")
        digest = hashlib.sha256()
        size = 0
        with urlopen(artifact.source_url, timeout=60) as response, destination.open(
            "xb"
        ) as output:
            final_url = urlparse(response.geturl())
            if (
                final_url.scheme != "https"
                or final_url.hostname not in _APPROVED_DOWNLOAD_HOSTS
            ):
                raise RuntimeError(
                    f"The component download redirected to an unapproved host: "
                    f"{final_url.hostname or '<unknown>'}"
                )
            while True:
                chunk = response.read(128 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > artifact.size_bytes:
                    raise RuntimeError("The component download exceeds its pinned size")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if size != artifact.size_bytes:
            raise RuntimeError("The component download size does not match its manifest")
        if digest.hexdigest() != artifact.sha256:
            raise RuntimeError("The component download failed SHA-256 verification")

    @staticmethod
    def _extract_zip(archive: Path, destination: Path) -> list[dict[str, Any]]:
        if not zipfile.is_zipfile(archive):
            raise RuntimeError("The component archive is not a valid ZIP file")
        seen: set[str] = set()
        planned_size = 0
        entries: list[tuple[zipfile.ZipInfo, Path]] = []
        with zipfile.ZipFile(archive) as package:
            for info in package.infolist():
                raw_name = info.filename.replace("\\", "/")
                relative = Path(raw_name)
                if (
                    not raw_name
                    or raw_name.startswith("/")
                    or relative.is_absolute()
                    or ".." in relative.parts
                ):
                    raise RuntimeError("The component archive contains an unsafe path")
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise RuntimeError("The component archive contains a symbolic link")
                normalized = relative.as_posix().rstrip("/")
                if not normalized:
                    continue
                duplicate_key = normalized.casefold()
                if duplicate_key in seen:
                    raise RuntimeError("The component archive contains duplicate paths")
                seen.add(duplicate_key)
                if len(seen) > _MAX_ARCHIVE_FILES:
                    raise RuntimeError("The component archive contains too many files")
                if not info.is_dir():
                    planned_size += info.file_size
                    if planned_size > _MAX_ARCHIVE_SIZE:
                        raise RuntimeError("The extracted component would be too large")
                entries.append((info, relative))
            installed: list[dict[str, Any]] = []
            for info, relative in entries:
                output_path = destination / relative
                resolved = output_path.resolve(strict=False)
                if destination.resolve(strict=False) not in resolved.parents:
                    raise RuntimeError("The component archive escapes its destination")
                if info.is_dir():
                    output_path.mkdir(parents=True, exist_ok=True)
                    continue
                output_path.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                size = 0
                with package.open(info) as source, output_path.open("xb") as output:
                    while True:
                        chunk = source.read(128 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > info.file_size:
                            raise RuntimeError("The extracted file exceeds its declared size")
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if size != info.file_size:
                    raise RuntimeError("The extracted file size is incomplete")
                os.chmod(output_path, 0o644)
                installed.append(
                    {
                        "path": relative.as_posix(),
                        "size": size,
                        "sha256": digest.hexdigest(),
                    }
                )
        return installed

    def verify_installed(self, component_id: str) -> tuple[bool, str]:
        manifest = self._read_manifest(component_id)
        if manifest is None:
            return False, "The component is not installed"
        files = manifest.get("files")
        if not isinstance(files, list) or not files:
            return False, "The component manifest has no files"
        root = self._component_path(component_id).resolve(strict=False)
        for entry in files:
            if not isinstance(entry, Mapping):
                return False, "The component manifest is invalid"
            relative = Path(str(entry.get("path", "")))
            if not relative.parts or relative.is_absolute() or ".." in relative.parts:
                return False, "The component manifest contains an unsafe path"
            path = (root / relative).resolve(strict=False)
            if root not in path.parents or not path.is_file() or path.is_symlink():
                return False, f"The component file is missing: {relative.as_posix()}"
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != str(entry.get("sha256", "")):
                return False, f"The component file failed verification: {relative.as_posix()}"
        return True, ""

    def install_bundled(self, component_id: str, bundled_root: Path) -> bool:
        """Install a component shipped inside the application (no download).

        Used for Voidek: the release bundles its pinned ONNX and JSON files.
        Nothing happens when the component is already installed and verified,
        or when this build does not bundle it; the files are hash-verified by
        ``install_local``.
        """
        definition = self._definition(component_id)
        source = Path(bundled_root) / component_id
        sources = {item.target_relative_path: source / item.target_relative_path for item in definition.pinned_files}
        if not sources or not all(path.is_file() for path in sources.values()):
            return False
        if self._read_manifest(component_id) is not None:
            return False
        self.install_local(component_id, sources)
        return True

    def install_local(self, component_id: str, sources: Mapping[str, Path]) -> None:
        """Install a component from local files that match its pinned size and SHA-256.

        ``sources`` maps each pinned target path to a local file. Nothing is
        downloaded; a file that does not match exactly is refused.
        """
        definition = self._definition(component_id)
        if definition.legacy or not definition.pinned_files:
            raise RuntimeError("This component has no pinned files for local installation")
        expected = {item.target_relative_path: item for item in definition.pinned_files}
        if set(sources) != set(expected):
            raise RuntimeError("Local installation needs exactly the pinned files")
        self.root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{component_id}-", dir=self.root))
        target = self._component_path(component_id)
        backup = self.root / f".{component_id}.previous"
        try:
            files: list[dict[str, Any]] = []
            for relative, pinned in sorted(expected.items()):
                source = Path(sources[relative])
                if source.is_symlink() or not source.is_file() or source.stat().st_size != pinned.size_bytes:
                    raise RuntimeError(f"The local file does not match the pinned size: {relative}")
                destination = staging / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                with source.open("rb") as reader, destination.open("xb") as writer:
                    for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                        digest.update(chunk)
                        writer.write(chunk)
                    writer.flush()
                    os.fsync(writer.fileno())
                if digest.hexdigest() != pinned.sha256:
                    raise RuntimeError(f"The local file failed verification: {relative}")
                os.chmod(destination, 0o644)
                files.append({"path": relative, "size": pinned.size_bytes, "sha256": pinned.sha256})
            _atomic_json_write(
                staging / "component.json",
                {
                    "schema_version": NARRATOR_COMPONENT_SCHEMA_VERSION,
                    "managed_by": "game-optimization-linux",
                    "component_id": component_id,
                    "kind": definition.kind.value,
                    "version": definition.version,
                    "license_id": definition.license_id,
                    "runtime_license_id": definition.runtime_license_id,
                    "artifact_license_id": definition.artifact_license_id,
                    "source": "local-pinned",
                    "attribution": definition.attribution,
                    "files": files,
                },
            )
            if backup.exists():
                shutil.rmtree(backup)
            if target.exists():
                os.replace(target, backup)
            try:
                os.replace(staging, target)
            except Exception:
                if backup.exists() and not target.exists():
                    os.replace(backup, target)
                raise
            if backup.exists():
                shutil.rmtree(backup)
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    def update(self, component_id: str) -> None:
        if self._read_manifest(component_id) is None:
            raise RuntimeError("The component is not installed")
        self.install(component_id)

    def remove(self, component_id: str) -> bool:
        self._definition(component_id)
        path = self._component_path(component_id)
        manifest = self._read_manifest(component_id)
        if manifest is None or manifest.get("managed_by") != "game-optimization-linux":
            raise RuntimeError("Refused to remove a component not managed by the application")
        resolved_root = self.root.resolve(strict=False)
        resolved_path = path.resolve(strict=False)
        if resolved_path.parent != resolved_root:
            raise RuntimeError("Refused to remove a component outside the narrator data root")
        shutil.rmtree(resolved_path)
        return True

    def record_installed(
        self,
        component_id: str,
        *,
        version: str,
        license_id: str,
        files: list[dict[str, Any]],
    ) -> Path:
        definition = self._definition(component_id)
        if not version.strip() or not license_id.strip():
            raise ValueError("component version and license are required")
        path = self._component_path(component_id) / "component.json"
        _atomic_json_write(
            path,
            {
                "schema_version": NARRATOR_COMPONENT_SCHEMA_VERSION,
                "managed_by": "game-optimization-linux",
                "component_id": component_id,
                "kind": definition.kind.value,
                "version": version,
                "license_id": license_id,
                "runtime_license_id": definition.runtime_license_id,
                "artifact_license_id": definition.artifact_license_id,
                "source_url": definition.source_url,
                "sha256": definition.sha256,
                "attribution": definition.attribution,
                "files": files,
            },
        )
        return path

    def _definition(self, component_id: str) -> NarratorComponentDefinition:
        try:
            return self._definitions[component_id]
        except KeyError as error:
            raise KeyError(f"unknown narrator component: {component_id}") from error

    def _component_path(self, component_id: str) -> Path:
        if not _COMPONENT_ID.fullmatch(component_id):
            raise ValueError("invalid narrator component id")
        return self.root / component_id

    def _read_manifest(self, component_id: str) -> dict[str, Any] | None:
        path = self._component_path(component_id) / "component.json"
        if not path.is_file():
            return None
        try:
            values = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(values, Mapping):
            return None
        if (
            values.get("schema_version") != NARRATOR_COMPONENT_SCHEMA_VERSION
            or values.get("component_id") != component_id
            or values.get("managed_by") != "game-optimization-linux"
        ):
            return None
        return dict(values)

    @staticmethod
    def _directory_size(path: Path) -> int:
        total = 0
        try:
            for item in path.rglob("*"):
                if item.is_file() and not item.is_symlink():
                    total += item.stat().st_size
        except OSError:
            return 0
        return total


__all__ = [
    "DEFAULT_NARRATOR_COMPONENTS",
    "NarratorComponentArtifact",
    "NarratorComponentDefinition",
    "NarratorComponentManager",
]
