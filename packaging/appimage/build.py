#!/usr/bin/env python3
"""Assemble fresh sources using pinned Python and the existing Flatpak wheels."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
PYTHON_URL = "https://github.com/astral-sh/python-build-standalone/releases/download/20261110/cpython-3.13.18%2B20261110-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
PYTHON_SHA256 = "f2d2199eb25880e07863b391cf7124e7ffa1338b0d65600dfcec117c4223a7fb"
TOOL_URL = "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
TOOL_SHA256 = "e8c1169c4af66c3184d7ee110713d4be04befe671fa358e2c1591ee840553b03"
RUNTIME_URL = "https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64"
RUNTIME_SHA256 = "617aa77314d57c8b8e4fae9c3f0b35af7b30d5529cb69e98f64f92510ed98a46"


def host_library(name: str) -> bool:
    """Do not interpose old Wayland/C++ libraries on a newer host Mesa driver."""
    return name.startswith((
        "libwayland-", "libEGL", "libGL", "libOpenGL", "libvulkan", "libgbm",
        "libdrm", "libstdc++", "libgcc_s", "libudev", "libc.so", "libm.so",
        "libdl.so", "libpthread", "librt.so", "libresolv", "libnss_", "ld-linux",
        "libfontconfig", "libfreetype",
    ))


def download(url: str, digest: str, cache: Path) -> Path:
    target = cache / url.rsplit("/", 1)[1].replace("%2B", "+")
    if not target.exists():
        temporary = target.with_suffix(target.suffix + ".part")
        urllib.request.urlretrieve(url, temporary)
        temporary.rename(target)
    if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        raise RuntimeError(f"Checksum mismatch: {target}; remove and retry")
    return target


def dependencies(library: Path) -> list[str]:
    result = subprocess.run(["readelf", "-d", str(library)], capture_output=True, text=True,
                            env=dict(os.environ, LC_ALL="C"), check=True)
    return re.findall(r"\(NEEDED\).*?\[(.*?)\]", result.stdout)


def required_versions(output: str, namespace: str) -> set[str]:
    """Read imports only, not the versions a library exports to its clients."""
    needs = output.split("Version needs section", 1)
    if len(needs) != 2:
        return set()
    return set(re.findall(rf"Name: {re.escape(namespace)}_(\d+(?:\.\d+)+)\b", needs[1]))


def abi_report(directory: Path) -> dict:
    files = []
    for path in sorted(directory.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open("rb") as handle:
            if handle.read(4) != b"\x7fELF":
                continue
        result = subprocess.run(["readelf", "-W", "--version-info", str(path)],
                                capture_output=True, text=True,
                                env=dict(os.environ, LC_ALL="C"), check=True)
        files.append({"path": str(path.relative_to(directory)), **{
            name: sorted(required_versions(result.stdout, name),
                         key=lambda value: tuple(map(int, value.split("."))))
            for name in ("GLIBC", "GLIBCXX", "CXXABI")}})
    glibc = [version for entry in files for version in entry["GLIBC"]]
    return {"elf_count": len(files), "max_glibc": max(glibc,
            key=lambda value: tuple(map(int, value.split("."))), default=None), "files": files}


def enforce_glibc(report: dict, ceiling: str) -> None:
    actual = report["max_glibc"]
    if actual is None:
        raise RuntimeError("No ELF GLIBC requirements found; refusing unchecked image")
    if tuple(map(int, actual.split("."))) > tuple(map(int, ceiling.split("."))):
        raise RuntimeError(f"GLIBC {actual} exceeds declared ceiling {ceiling}")


def assemble(args: argparse.Namespace) -> None:
    import yaml
    appdir = args.work / f"AppDir-{args.edition}"
    if appdir.exists():
        raise RuntimeError(f"Refusing to overwrite {appdir}; use a new work directory")
    args.cache.mkdir(parents=True, exist_ok=True)
    appdir.mkdir(parents=True)
    with tarfile.open(download(PYTHON_URL, PYTHON_SHA256, args.cache)) as archive:
        archive.extractall(args.work / "python-extracted", filter="data")
    shutil.move(str(args.work / "python-extracted/python"), appdir / "usr")
    python = appdir / "usr/bin/python3"
    wheels = args.cache / "wheels"
    wheels.mkdir(exist_ok=True)
    manifest = yaml.safe_load((ROOT / "flatpak/io.github.DevVoidPL.GameOptimizationLinux.yml").read_text())
    for module in manifest["modules"]:
        if module["name"] == "python-runtime-dependencies":
            for source in module["sources"]:
                shutil.copy2(download(source["url"], source["sha256"], args.cache), wheels)
    subprocess.run([str(python), "-m", "pip", "install", "--no-index", f"--find-links={wheels}",
                    "PySide6==6.11.1", "py7zr==1.1.3", "PyYAML==6.0.3"], check=True)
    site = appdir / "usr/lib/python3.13/site-packages"
    shutil.rmtree(site / "psutil/tests", ignore_errors=True)
    bindings = {"QtCore", "QtGui", "QtWidgets", "QtQml", "QtQuick", "QtMultimedia", "QtNetwork", "QtOpenGL", "QtSvg"}
    for path in (site / "PySide6").glob("Qt*.abi3.so"):
        if path.name.split(".", 1)[0] not in bindings:
            path.unlink()
    qt = site / "PySide6/Qt"
    # Do not ship unrelated WebEngine/SQL/3D plugins which can dlopen host Qt
    # modules. Keep the QML modules actually used by this application.
    qml_modules = {"Qt", "Qt5Compat", "QtCore", "QtQml", "QtQuick", "QtMultimedia", "QtNetwork"}
    plugin_types = {"platforms", "platforminputcontexts", "platformthemes", "iconengines", "imageformats",
                    "multimedia", "networkinformation", "tls", "wayland-decoration-client",
                    "wayland-graphics-integration-client", "wayland-shell-integration", "xcbglintegrations"}
    for path in (qt / "qml").iterdir():
        if path.is_dir() and path.name not in qml_modules:
            shutil.rmtree(path)
    for path in (qt / "plugins").iterdir():
        if path.is_dir() and path.name not in plugin_types:
            shutil.rmtree(path)
    # Design-time QML helpers are not present in the runtime wheel's Qt libs.
    for path in (qt / "qml").rglob("*DesignHelpers*"):
        if path.is_dir():
            shutil.rmtree(path)
    for path in (qt / "plugins/platformthemes").glob("*gtk*"):
        path.unlink()
    for pattern in ("libqeglfs*", "libqlinuxfb*", "libqvkkhrdisplay*", "libqminimalegl*"):
        for path in (qt / "plugins/platforms").glob(pattern):
            path.unlink()
    # Retain a complete dependency closure of the remaining Qt shared objects.
    qt_libraries = {p.name: p for p in (qt / "lib").glob("*.so*")}
    needed = set()
    pending = [p for directory in (qt / "qml", qt / "plugins") for p in directory.rglob("*.so")]
    pending += [site / "PySide6" / name for name in
                ("QtCore.abi3.so", "QtGui.abi3.so", "QtWidgets.abi3.so", "QtQml.abi3.so", "QtQuick.abi3.so", "QtMultimedia.abi3.so")]
    while pending:
        path = pending.pop()
        for name in dependencies(path):
            if name in qt_libraries and name not in needed:
                needed.add(name)
                pending.append(qt_libraries[name])
    for name, path in qt_libraries.items():
        if name not in needed:
            path.unlink()
    package = site / "game_optimization_linux"
    shutil.copytree(ROOT / "src/game_optimization_linux", package,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.ts"))
    if args.edition == "desktop":
        (package / "edition.txt").write_text("desktop\n")
        for relative in ("qml/couch", "assets/ui-icons/couch", "assets/audio"):
            shutil.rmtree(package / relative)
    # Reuse the very same checksum-pinned decoder source as Flatpak, compiled
    # in the declared baseline (never a rolling host build).
    sevenzip = next(m for m in manifest["modules"] if m["name"] == "sevenzip-bcj2-decoder")["sources"][0]
    source = download(sevenzip["url"], sevenzip["sha256"], args.cache)
    sevenzip_work = args.work / "sevenzip"
    with tarfile.open(source) as archive:
        archive.extractall(sevenzip_work, filter="data")
    subprocess.run(["make", "-f", "../../cmpl_gcc.mak", "-j6"],
                   cwd=sevenzip_work / "CPP/7zip/Bundles/Alone2", check=True)
    (appdir / "usr/libexec").mkdir()
    shutil.copy2(sevenzip_work / "CPP/7zip/Bundles/Alone2/b/g/7zz", appdir / "usr/libexec/game-optimization-7zz")
    # Optional PDF/TIFF plugins refer to absent QtPdf/old TIFF and may borrow
    # incompatible host Qt modules. Neither is an application artwork format.
    plugins = site / "PySide6/Qt/plugins/imageformats"
    for name in ("libqpdf.so", "libqtiff.so"):
        (plugins / name).unlink(missing_ok=True)
    candidates = {p.name: p for p in args.library_root.rglob("*.so*") if p.is_file()}
    # SDL is dlopened by the controller provider, not linked to Python or Qt.
    if args.edition == "couch" and "libSDL3.so.0" in candidates:
        shutil.copy2(candidates["libSDL3.so.0"].resolve(), appdir / "usr/lib/libSDL3.so.0")
    elif args.edition == "couch":
        raise RuntimeError("Baseline-built SDL3 is required for the Couch edition")
    queue = [p for p in (appdir / "usr").rglob("*") if p.is_file() and not p.is_symlink()
             and (".so" in p.name or p.name == "python3.13")]
    # Pre-index SONAME symlinks; a wheel may contain an unversioned real file.
    bundled = {p.name for p in (appdir / "usr").rglob("*.so*")}
    visited = set()
    missing = set()
    while queue:
        library = queue.pop()
        if library.name in visited:
            continue
        visited.add(library.name)
        if library.read_bytes()[:4] != b"\x7fELF":
            continue
        for name in dependencies(library):
            if name.startswith("$ORIGIN/") or host_library(name) or name in bundled:
                continue
            if name in candidates:
                target = appdir / "usr/lib" / name
                shutil.copy2(candidates[name].resolve(), target)
                queue.append(target)
                bundled.add(name)
            else:
                missing.add(name)
    shutil.copy2(ROOT / "packaging/appimage/AppRun", appdir / "AppRun")
    (appdir / "AppRun").chmod(0o755)
    app_id = manifest["app-id"]
    shutil.copy2(ROOT / f"data/{app_id}.desktop", appdir)
    desktop = appdir / f"{app_id}.desktop"
    desktop.write_text(desktop.read_text().replace("Exec=game-optimization-linux", "Exec=AppRun"))
    shutil.copy2(ROOT / f"data/icons/hicolor/256x256/apps/{app_id}.png", appdir / f"{app_id}.png")
    (appdir / ".DirIcon").symlink_to(f"{app_id}.png")
    notices = appdir / "usr/share/licenses/gameopti"
    notices.mkdir(parents=True)
    shutil.copy2(ROOT / "LICENSE", notices)
    shutil.copy2(ROOT / "packaging/appimage/README.md", notices / "PACKAGING.md")
    shutil.copytree(sevenzip_work / "DOC", notices / "7zip")
    # Preserve the distro copyright/license notices of copied baseline libs.
    baseline_share = args.library_root.parent / "share"
    for source in (baseline_share / "doc").glob("*/copyright"):
        target = notices / "baseline" / source.parent.name / "copyright"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    if (baseline_share / "licenses/SDL3").exists():
        shutil.copytree(baseline_share / "licenses/SDL3", notices / "SDL3")
    version = re.search(r'APP_VERSION = "([^"]+)"', (package / "config.py").read_text())[1]
    report = {"version": version, "edition": args.edition, "library_root": str(args.library_root),
              "unresolved_libraries": sorted(missing), "python_sha256": PYTHON_SHA256,
              "tool_sha256": TOOL_SHA256, "runtime_sha256": RUNTIME_SHA256}
    (args.work / "assembly-report.json").write_text(json.dumps(report, indent=2) + "\n")
    if missing:
        raise RuntimeError(f"Unresolved bundled libraries: {sorted(missing)}")
    abi = abi_report(appdir)
    (args.work / "abi-report.json").write_text(json.dumps(abi, indent=2) + "\n")
    enforce_glibc(abi, args.max_glibc)
    tool = download(TOOL_URL, TOOL_SHA256, args.cache)
    tool.chmod(0o755)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ, ARCH="x86_64", APPIMAGE_EXTRACT_AND_RUN="1", VERSION=version)
    runtime = download(RUNTIME_URL, RUNTIME_SHA256, args.cache)
    # The executable AppImage runtime itself must satisfy the same ABI ceiling.
    runtime_check = args.work / "runtime-abi"
    runtime_check.mkdir()
    shutil.copy2(runtime, runtime_check / "runtime")
    runtime_abi = abi_report(runtime_check)
    (args.work / "runtime-abi-report.json").write_text(json.dumps(runtime_abi, indent=2) + "\n")
    if runtime_abi["max_glibc"] is not None:
        enforce_glibc(runtime_abi, args.max_glibc)
    subprocess.run([str(tool), "--runtime-file", str(runtime), "--no-appstream", str(appdir), str(args.output)], env=environment, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--library-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--edition", choices=("couch", "desktop"), default="couch")
    parser.add_argument("--max-glibc", default="2.35", help="Reject any newer imported GLIBC symbol")
    assemble(parser.parse_args())


if __name__ == "__main__":
    main()