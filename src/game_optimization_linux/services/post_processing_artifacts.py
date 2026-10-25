"""On-demand verified downloads. No upstream DLLs or shaders are shipped."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import shutil
from zipfile import ZipFile

from .fsr_variants import FsrArtifactClient, PinnedArchive

RESHADE_VERSION = "6.8.0"
SHADER_COMMIT = "fd0022170615ce0d8162d219bff07232fa6dd84f"
SWEETFX_COMMIT = "93ddf39b357f5da534ed6d34ba4ec8cc7dcfa361"
SETUP = PinnedArchive("reshade-6.8.0", "https://reshade.me/downloads/ReShade_Setup_6.8.0.exe", 4075064,
                      "207aea16205fbf952bc8fe1879966672454cf04002e7ad34237c7990a5b3c0b4")
RESHADE_LICENCE = PinnedArchive("reshade-licence", "https://raw.githubusercontent.com/crosire/reshade/v6.8.0/LICENSE.md",
                              1481, "237ded5b8344f820113efab1e65e91e1f159d9202c5b4856606a0590d3ffdab0")
SHADERS = PinnedArchive("reshade-headers", f"https://codeload.github.com/crosire/reshade-shaders/zip/{SHADER_COMMIT}",
                        58091, "a3b110ba5118f3b944d74f0b0746c21280071d389ed98d615bf3c4b3a1778586")
SWEETFX = PinnedArchive("sweetfx", f"https://codeload.github.com/CeeJayDK/SweetFX/zip/{SWEETFX_COMMIT}",
                        123972, "e1e1d6515d29c65fcf115c9692a1c5f91ffb8d9734e6871bcf67ac48588dbbce")
DLL_HASHES = {
    32: "538998f66c0197adcdeadfe8e5ea19dd6ca3253e40399476d291f385ce518b88",
    64: "b2945c29e7095491a901746b400e58db9b1592ab092bacf2a888ce37f02d08da",
}
VKBASALT_VERSION = "0.3.2.10"
VKBASALT_REF = "runtime/org.freedesktop.Platform.VulkanLayer.vkBasalt/x86_64/25.08"
VKBASALT_HASHES = {
    64: "7f1a8be3f9d590c5913fef129fbbd6bfd8809455d04fafdb4c413356846d3ec6",
    32: "021df415ecdf2766c4063d121dd7c0f7a980d12cee1cd59511f26f41e38eeac0",
}


class PostProcessingArtifacts:
    def __init__(self, cache: Path, host=None, client=None):
        self.cache = Path(cache)
        self.host = host
        self.client = client or FsrArtifactClient(self.cache)

    def shaders(self, target: Path) -> None:
        # Preserve upstream headers, includes, textures and licence notices.
        for pinned, prefix in ((SHADERS, f"reshade-shaders-{SHADER_COMMIT}/"),
                               (SWEETFX, f"SweetFX-{SWEETFX_COMMIT}/")):
            archive = self.client._download(pinned)
            with ZipFile(archive) as z:
                for item in z.infolist():
                    if item.is_dir() or not item.filename.startswith(prefix):
                        continue
                    relative = Path(item.filename[len(prefix):])
                    if relative.is_absolute() or ".." in relative.parts or item.file_size > 4 * 1024 * 1024:
                        raise ValueError("Invalid shader archive entry")
                    if relative.parts[0] not in {"Shaders", "Textures"}:
                        relative = Path("Licences") / pinned.key / relative
                    path = target / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(z.read(item))
        # SweetFX includes ReShade headers by name; SMAA uses unprefixed texture names.
        for path in (target / "Shaders").glob("*.fxh"):
            shutil.copy2(path, target / "Shaders/SweetFX" / path.name)
        (target / "SOURCES.txt").write_text(
            f"ReShade: https://reshade.me/ ({RESHADE_VERSION}, standard signed build)\n"
            f"Headers: https://github.com/crosire/reshade-shaders/tree/{SHADER_COMMIT}\n"
            f"SweetFX: https://github.com/CeeJayDK/SweetFX/tree/{SWEETFX_COMMIT} (MIT; see Licences)\n"
            "vkBasalt: https://github.com/DadSchoorse/vkBasalt (Zlib), Flathub 25.08 package\n",
            encoding="utf-8")

    def reshade(self, bits: int, target: Path) -> None:
        archive = self.client._download(SETUP)
        with ZipFile(archive) as z:
            data = z.read(f"ReShade{bits}.dll")
        if sha256(data).hexdigest() != DLL_HASHES[bits]:
            raise ValueError("ReShade DLL checksum mismatch")
        target.write_bytes(data)
        licence = self.client._download(RESHADE_LICENCE)
        directory = target.parent / ".gameopti-post-processing/Licences"
        directory.mkdir(parents=True, exist_ok=True)
        shutil.copy2(licence, directory / "ReShade-LICENSE.md")

    def vkbasalt(self, bits: int, target: Path) -> dict:
        if self.host is None:
            from .host_service import HostServiceClient
            self.host = HostServiceClient()
        directory = self.host.post_processing_layer_directory(install=True)
        architecture = "x86_64" if bits == 64 else "i386"
        source = Path(directory) / f"lib/{architecture}-linux-gnu/vkbasalt/libvkbasalt.so"
        relative = f"lib/{architecture}-linux-gnu/vkbasalt/libvkbasalt.so"
        data = source.read_bytes() if source.is_file() else self.host.post_processing_component(relative)
        if sha256(data).hexdigest() != VKBASALT_HASHES[bits]:
            raise ValueError(f"Verified vkBasalt {VKBASALT_VERSION} {bits}-bit library is unavailable")
        target.write_bytes(data)
        target.chmod(0o755)
        licences = Path(directory) / "share/licenses/org.freedesktop.Platform.VulkanLayer.vkBasalt"
        for name in ("vkBasalt", "vkBasalt-32bit", "spirv-headers"):
            notice = licences / name / "LICENSE"
            text = notice.read_bytes() if notice.is_file() else self.host.post_processing_component(
                "share/licenses/org.freedesktop.Platform.VulkanLayer.vkBasalt/" + name + "/LICENSE")
            destination = target.parent / "Licences/vkBasalt" / name / "LICENSE"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(text)
        return {"source": str(source), "version": VKBASALT_VERSION, "bits": bits}
