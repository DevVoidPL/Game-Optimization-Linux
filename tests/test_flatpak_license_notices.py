"""Keep standalone dependency notices in both generated Flatpak manifests."""
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("suffix", ["", ".desktop-edition"])
def test_flatpak_keeps_dependency_license_notices(suffix):
    path = ROOT / "flatpak" / f"io.github.DevVoidPL.GameOptimizationLinux{suffix}.yml"
    manifest = yaml.safe_load(path.read_text())
    modules = {module["name"]: module for module in manifest["modules"]}
    required = {
        "sevenzip-bcj2-decoder": ["DOC/License.txt", "DOC/copying.txt", "DOC/unRarLicense.txt"],
        "mit-krb5": ["krb5-1.22.2/NOTICE", "krb5-1.22.2/src/lib/gssapi/LICENSE"],
        "python-runtime-dependencies": ["LGPL-3.0-only.txt", "GPL-3.0-only.txt"],
    }
    for name, notices in required.items():
        module = modules[name]
        installs = "\n".join(command for command in module["build-commands"]
                             if "install -Dm644" in command and "/share/licenses/" in command)
        for notice in notices:
            assert notice in installs, (name, notice)
        assert "/share" not in module.get("cleanup", []), name


def test_pyside_license_downloads_are_versioned_and_pinned():
    manifest = yaml.safe_load((ROOT / "flatpak/io.github.DevVoidPL.GameOptimizationLinux.yml").read_text())
    dependency = next(module for module in manifest["modules"] if module["name"] == "python-runtime-dependencies")
    sources = [source for source in dependency["sources"] if source.get("dest") == "pyside-licenses"]
    assert {source["dest-filename"] for source in sources} >= {"LGPL-3.0-only.txt", "GPL-3.0-only.txt"}
    for source in sources:
        assert source["url"].startswith("https://code.qt.io/cgit/pyside/pyside-setup.git/plain/LICENSES/")
        assert source["url"].endswith("?h=v6.11.1")
        assert len(source["sha256"]) == 64