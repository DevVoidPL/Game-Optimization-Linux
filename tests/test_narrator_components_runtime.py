from __future__ import annotations

from io import BytesIO
import hashlib
from pathlib import Path
import zipfile

import pytest

from game_optimization_linux.models.narrator import (
    NarratorComponentKind,
    NarratorComponentState,
)
from game_optimization_linux.services.narrator_components import (
    NarratorComponentArtifact,
    NarratorComponentDefinition,
    NarratorComponentManager,
)


class _Response(BytesIO):
    def __init__(self, payload: bytes, url: str) -> None:
        super().__init__(payload)
        self._url = url

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _zip_payload(files: dict[str, bytes]) -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
    return output.getvalue()


def test_multi_artifact_component_is_verified_and_installed_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = b"voice-model"
    second = b'{"audio":{"sample_rate":22050}}'
    urls = {
        "https://huggingface.co/example/voice.onnx": first,
        "https://huggingface.co/example/voice.onnx.json": second,
    }
    definition = NarratorComponentDefinition(
        component_id="tts.test-voice",
        kind=NarratorComponentKind.TTS,
        name="Test voice",
        license_id="MIT",
        version="1",
        download_size_bytes=len(first) + len(second),
        install_ready=True,
        artifacts=tuple(
            NarratorComponentArtifact(
                source_url=url,
                sha256=hashlib.sha256(payload).hexdigest(),
                size_bytes=len(payload),
                target_relative_path=(
                    "voices/test.onnx.json" if url.endswith(".json") else "voices/test.onnx"
                ),
            )
            for url, payload in urls.items()
        ),
    )
    monkeypatch.setattr(
        "game_optimization_linux.services.narrator_components.urlopen",
        lambda url, **_values: _Response(urls[str(url)], str(url)),
    )
    manager = NarratorComponentManager(tmp_path, definitions=(definition,))

    manager.install(definition.component_id)

    assert (tmp_path / definition.component_id / "voices/test.onnx").read_bytes() == first
    assert (
        tmp_path / definition.component_id / "voices/test.onnx.json"
    ).read_bytes() == second
    assert manager.verify_installed(definition.component_id) == (True, "")


def test_verified_argos_archive_is_safely_extracted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _zip_payload(
        {
            "translate-en_pl-1_9/metadata.json": b'{"from_code":"en","to_code":"pl","package_version":"1.9"}',
            "translate-en_pl-1_9/model/config.json": b"{}",
            "translate-en_pl-1_9/model/model.bin": b"model",
            "translate-en_pl-1_9/bpe.model": b"#version: 0.2\n",
        }
    )
    url = "https://data.argosopentech.com/argospm/v1/test.argosmodel"
    definition = NarratorComponentDefinition(
        component_id="translation.test",
        kind=NarratorComponentKind.TRANSLATION,
        name="Test translator",
        license_id="CC-BY-4.0",
        version="1",
        download_size_bytes=len(payload),
        install_ready=True,
        artifacts=(
            NarratorComponentArtifact(
                source_url=url,
                sha256=hashlib.sha256(payload).hexdigest(),
                size_bytes=len(payload),
                archive_format="zip",
            ),
        ),
    )
    monkeypatch.setattr(
        "game_optimization_linux.services.narrator_components.urlopen",
        lambda *_args, **_values: _Response(payload, url),
    )
    manager = NarratorComponentManager(tmp_path, definitions=(definition,))

    manager.install(definition.component_id)

    model = tmp_path / definition.component_id / "translate-en_pl-1_9/model/model.bin"
    assert model.read_bytes() == b"model"
    assert manager.verify_installed(definition.component_id) == (True, "")


def test_component_archive_rejects_traversal_before_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _zip_payload({"../escaped": b"bad"})
    url = "https://data.argosopentech.com/argospm/v1/bad.argosmodel"
    definition = NarratorComponentDefinition(
        component_id="translation.bad",
        kind=NarratorComponentKind.TRANSLATION,
        name="Bad translator",
        license_id="test",
        version="1",
        download_size_bytes=len(payload),
        install_ready=True,
        artifacts=(
            NarratorComponentArtifact(
                source_url=url,
                sha256=hashlib.sha256(payload).hexdigest(),
                size_bytes=len(payload),
                archive_format="zip",
            ),
        ),
    )
    monkeypatch.setattr(
        "game_optimization_linux.services.narrator_components.urlopen",
        lambda *_args, **_values: _Response(payload, url),
    )
    manager = NarratorComponentManager(tmp_path, definitions=(definition,))

    with pytest.raises(RuntimeError, match="unsafe path"):
        manager.install(definition.component_id)

    assert not (tmp_path / definition.component_id).exists()
    assert not (tmp_path / "escaped").exists()


def test_failed_update_keeps_previous_verified_component(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = b"original"
    replacement = b"replacement"
    url = "https://raw.githubusercontent.com/example/model.bin"
    definition = NarratorComponentDefinition(
        component_id="ocr.atomic",
        kind=NarratorComponentKind.OCR,
        name="Atomic model",
        license_id="test",
        version="1",
        download_size_bytes=len(replacement),
        source_url=url,
        sha256=hashlib.sha256(b"different").hexdigest(),
        target_relative_path="model.bin",
        install_ready=True,
    )
    manager = NarratorComponentManager(tmp_path, definitions=(definition,))
    manager.record_installed(
        definition.component_id,
        version="old",
        license_id="test",
        files=[
            {
                "path": "model.bin",
                "size": len(original),
                "sha256": hashlib.sha256(original).hexdigest(),
            }
        ],
    )
    installed = tmp_path / definition.component_id / "model.bin"
    installed.write_bytes(original)
    monkeypatch.setattr(
        "game_optimization_linux.services.narrator_components.urlopen",
        lambda *_args, **_values: _Response(replacement, url),
    )

    with pytest.raises(RuntimeError, match="SHA-256"):
        manager.update(definition.component_id)

    assert installed.read_bytes() == original
    assert manager.verify_installed(definition.component_id) == (True, "")


def test_corrupted_installed_component_fails_integrity_check(tmp_path: Path) -> None:
    definition = NarratorComponentDefinition(
        component_id="tts.corrupt",
        kind=NarratorComponentKind.TTS,
        name="Corrupt voice",
        license_id="test",
    )
    manager = NarratorComponentManager(tmp_path, definitions=(definition,))
    manager.record_installed(
        definition.component_id,
        version="1",
        license_id="test",
        files=[
            {
                "path": "voice.onnx",
                "size": 4,
                "sha256": hashlib.sha256(b"good").hexdigest(),
            }
        ],
    )
    (tmp_path / definition.component_id / "voice.onnx").write_bytes(b"evil")

    verified, message = manager.verify_installed(definition.component_id)

    assert verified is False
    assert "failed verification" in message


def test_managed_older_component_exposes_verified_update_and_attribution(
    tmp_path: Path,
) -> None:
    definition = NarratorComponentDefinition(
        component_id="translation.versioned",
        kind=NarratorComponentKind.TRANSLATION,
        name="Versioned translator",
        license_id="CC-BY-4.0",
        version="2",
        download_size_bytes=5,
        source_url="https://raw.githubusercontent.com/example/model.bin",
        sha256=hashlib.sha256(b"model").hexdigest(),
        target_relative_path="model.bin",
        attribution="Model authors",
        install_ready=True,
    )
    manager = NarratorComponentManager(tmp_path, definitions=(definition,))
    manager.record_installed(
        definition.component_id,
        version="1",
        license_id="CC-BY-4.0",
        files=[
            {
                "path": "model.bin",
                "size": 5,
                "sha256": hashlib.sha256(b"model").hexdigest(),
            }
        ],
    )
    (tmp_path / definition.component_id / "model.bin").write_bytes(b"model")
    manager.set_runtime_state(definition.component_id, True, "ready")

    component = manager.status(definition.component_id)

    assert component.state is NarratorComponentState.UPDATE_AVAILABLE
    assert component.update_version == "2"
    assert component.attribution == "Model authors"
