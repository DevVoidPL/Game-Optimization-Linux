"""Private, opt-in installations of pinned upstream frame-generation packages.

No manifests are registered globally. Packages remain separate from installations
owned by a distribution, GOverlay, Decky or the user.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import shutil
import tarfile
from threading import Event
from urllib.request import Request, urlopen
from uuid import uuid4

from .fsr_variants import PinnedArchive
from .host_bootstrap import host_home_directory
from .mangohud import _atomic_write

PACKAGES = {
    "lsfg": PinnedArchive("lsfg-vk-2.0.0", "https://builds.lsfg-vk.dev/lsfg-vk-2.0.0.tar.xz", 561348,
                          "08bdbdf373a111022df87dac7aa87e3b564bb841f961552e3ca85fea12b5aa74"),
    "mako": PinnedArchive("mako-4.6.0", "https://github.com/eugeniosegala/MAKO/releases/download/render-v4.6.0/MAKO-Renderer-v4.6.0-linux.tar.xz", 2890224,
                          "66224414e2d94f7cb019226735b3b6d26b9be7103c5334250522f7260213be56"),
    "afmf": PinnedArchive("afmf-1.3.1", "https://github.com/serialexperimentslainnnn/afmf-linux/releases/download/v1.3.1/afmf-linux-1.3.1-x86_64.tar.xz", 91064,
                          "7fafa09021ee94dc5ec8da694cb02fe298b54c114b486611bb24b1e1500edb18"),
}
VERSIONS = {"lsfg": "2.0.0", "mako": "4.6.0", "afmf": "1.3.1"}
LAYER_NAMES = {"lsfg": "VK_LAYER_LSFGVK_frame_generation", "mako": "VK_LAYER_MAKO_render", "afmf": "VK_LAYER_AFMF"}


def file_hash(path):
    return sha256(Path(path).read_bytes()).hexdigest()


class Cancelled(RuntimeError):
    pass


class FrameGenerationArtifacts:
    def __init__(self, root=None, opener=None):
        self.root = Path(root) if root else host_home_directory() / ".local/share/game-optimization-linux/frame-generation"
        self.opener = opener or urlopen

    def directory(self, backend):
        if backend not in PACKAGES:
            raise ValueError("Unknown frame-generation backend")
        return self.root / backend

    def status(self, backend):
        directory = self.directory(backend)
        result = {"installed": False, "managed": False, "version": "", "availableVersion": VERSIONS[backend],
                  "downloadSize": PACKAGES[backend].size, "source": PACKAGES[backend].url}
        if (self.root / (".transaction-" + backend + ".json")).exists():
            return {**result, "error": "Interrupted component transaction; retry installation to recover"}
        if not directory.exists():
            return result
        manifest = directory / "gameopti-install.json"
        if directory.is_symlink() or not manifest.is_file() or manifest.is_symlink():
            return {**result, "error": "Private component directory is occupied by unowned files"}
        record = json.loads(manifest.read_text())
        if record.get("owner") != "gameopti-frame-generation":
            return {**result, "error": "Component belongs to another installer"}
        for relative, expected in record["hashes"].items():
            path = Path(relative)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Invalid component ownership manifest")
            target = directory / path
            if target.is_symlink() or not target.is_file() or file_hash(target) != expected:
                return {**result, "managed": True, "error": f"Component missing or modified: {relative}"}
        actual = {str(p.relative_to(directory)) for p in directory.rglob("*") if p.is_file() or p.is_symlink()}
        if actual != set(record["hashes"]) | {"gameopti-install.json"}:
            return {**result, "managed": True, "error": "Unknown files in component directory; update/removal stopped"}
        return {**result, "installed": True, "managed": True, "version": record["version"],
                "updateAvailable": record["version"] != VERSIONS[backend]}

    def _recover(self, backend):
        journal = self.root / (".transaction-" + backend + ".json")
        if not journal.exists(): return
        if journal.is_symlink(): raise ValueError("Invalid component transaction journal")
        record = json.loads(journal.read_text())
        names = [record.get("staging", ""), record.get("backup", "")]
        if any(Path(n).name != n or not n.startswith(prefix + backend + "-") for n, prefix in zip(names, (".stage-", ".previous-"))):
            raise ValueError("Invalid component transaction journal")
        staging, backup = [self.root / n for n in names]
        directory = self.directory(backend)
        if any(p.is_symlink() for p in (staging, backup, directory)): raise ValueError("Invalid component transaction journal")
        # Never remove files added independently during interruption.
        for path in (staging, backup, directory):
            if not path.exists(): continue
            manifest = json.loads((path / "gameopti-install.json").read_text())
            actual = {str(p.relative_to(path)) for p in path.rglob("*") if p.is_file() or p.is_symlink()}
            if manifest.get("owner") != "gameopti-frame-generation" or actual != set(manifest["hashes"]) | {"gameopti-install.json"}:
                raise ValueError("Unknown files in interrupted transaction; recovery stopped")
            for relative, digest in manifest["hashes"].items():
                if Path(relative).is_absolute() or ".." in Path(relative).parts or file_hash(path / relative) != digest:
                    raise ValueError("Modified files in interrupted transaction; recovery stopped")
        if not directory.exists() and backup.exists(): backup.rename(directory)
        elif backup.exists(): shutil.rmtree(backup)
        if staging.exists(): shutil.rmtree(staging)
        journal.unlink()

    def _download(self, backend, cancel, progress):
        pinned = PACKAGES[backend]
        cache = self.root / "downloads"
        cache.mkdir(parents=True, exist_ok=True)
        target = cache / Path(pinned.url).name
        if target.is_file() and not target.is_symlink() and target.stat().st_size == pinned.size and file_hash(target) == pinned.sha256:
            return target
        temporary = cache / (".download-" + uuid4().hex)
        try:
            request = Request(pinned.url, headers={"User-Agent": "GameOpti/2.0", "Accept": "application/octet-stream"})
            count = 0
            digest = sha256()
            with self.opener(request, timeout=20) as response, temporary.open("xb") as output:
                while True:
                    if cancel.is_set(): raise Cancelled("Download cancelled; installation unchanged")
                    chunk = response.read(65536)
                    if not chunk: break
                    count += len(chunk)
                    if count > pinned.size: raise ValueError("Download exceeds verified size")
                    digest.update(chunk); output.write(chunk)
                    progress("download", count / pinned.size)
            if count != pinned.size or digest.hexdigest() != pinned.sha256:
                raise ValueError("Component download failed size/SHA-256 verification")
            temporary.replace(target)
            return target
        finally:
            temporary.unlink(missing_ok=True)

    def install(self, backend, cancel=None, progress=None):
        cancel = cancel or Event()
        progress = progress or (lambda *_: None)
        self.directory(backend)  # Validate before constructing recovery paths.
        self._recover(backend)
        before = self.status(backend)
        if before.get("error"): raise ValueError(before["error"])
        archive = self._download(backend, cancel, progress)
        directory = self.directory(backend)
        staging = self.root / (".stage-" + backend + "-" + uuid4().hex)
        backup = self.root / (".previous-" + backend + "-" + uuid4().hex)
        journal = self.root / (".transaction-" + backend + ".json")
        staging.mkdir()
        try:
            total = 0
            with tarfile.open(archive) as stream:
                for member in stream:
                    if cancel.is_set(): raise Cancelled("Installation cancelled; previous component preserved")
                    relative = Path(member.name)
                    if relative.is_absolute() or ".." in relative.parts or member.issym() or member.islnk():
                        raise ValueError("Unsafe component archive member")
                    if member.isdir(): continue
                    total += member.size
                    if not member.isfile() or total > 200 * 1024 * 1024:
                        raise ValueError("Invalid component archive")
                    target = staging / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with stream.extractfile(member) as source, target.open("xb") as output:
                        shutil.copyfileobj(source, output)
                    target.chmod(0o755 if member.mode & 0o111 else 0o644)
            # Keep the official MAKO layout: mako-launch derives its prefix.
            if backend == "afmf":
                nested = staging / "afmf-linux-1.3.1"
                for path in tuple(nested.iterdir()): path.rename(staging / path.name)
                nested.rmdir()
            layers = staging / "share/vulkan/implicit_layer.d"
            layers.mkdir(parents=True, exist_ok=True)
            if backend == "afmf":
                data = json.loads((staging / "afmf-linux.json").read_text())
                data["layer"]["library_path"] = "../../../libafmf-linux.so"
                (layers / "afmf-linux.json").write_text(json.dumps(data, indent=2))
            hashes = {str(p.relative_to(staging)): file_hash(p) for p in staging.rglob("*") if p.is_file()}
            if not any(p.suffix == ".so" for p in staging.rglob("*")):
                raise ValueError("Package does not contain a Vulkan layer")
            _atomic_write(staging / "gameopti-install.json", json.dumps({"owner": "gameopti-frame-generation",
                "backend": backend, "version": VERSIONS[backend], "source": PACKAGES[backend].url,
                "archiveSha256": PACKAGES[backend].sha256, "hashes": hashes}, indent=2))
            if cancel.is_set(): raise Cancelled("Installation cancelled; previous component preserved")
            progress("install", 0.95)
            _atomic_write(journal, json.dumps({"staging": staging.name, "backup": backup.name}))
            if directory.exists(): directory.rename(backup)
            try: staging.rename(directory)
            except Exception:
                if backup.exists(): backup.rename(directory)
                journal.unlink(missing_ok=True)
                raise
            if backup.exists(): shutil.rmtree(backup)
            journal.unlink(missing_ok=True)
            progress("complete", 1.0)
            return self.status(backend)
        finally:
            if staging.exists(): shutil.rmtree(staging)

    def remove(self, backend):
        state = self.status(backend)
        if state.get("error"): raise ValueError(state["error"])
        if state["managed"]:
            directory = self.directory(backend)
            # Refuse unknown files rather than deleting presets/modifications.
            known = set(json.loads((directory / "gameopti-install.json").read_text())["hashes"]) | {"gameopti-install.json"}
            actual = {str(p.relative_to(directory)) for p in directory.rglob("*") if p.is_file() or p.is_symlink()}
            if actual != known: raise ValueError("Unknown files in component directory; removal stopped")
            shutil.rmtree(directory)
        # Per-game profiles, imported files, caches and proprietary DLLs survive.
