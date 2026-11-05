#!/usr/bin/env python3
"""Finalize and audit a fresh build-only Desktop tree before direct export."""
import argparse
from pathlib import Path
import subprocess

import yaml

from desktop_release import require_clean


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", required=True, type=Path)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--bundle", required=True, type=Path)
    args = parser.parse_args()
    build = args.build_dir.resolve()
    manifest = yaml.safe_load((Path(__file__).resolve().parent / "io.github.DevVoidPL.GameOptimizationLinux.desktop-edition.yml").read_text())
    files = build / "files"
    require_clean(files)
    subprocess.run(["flatpak", "build-finish", f"--command={manifest['command']}", *manifest["finish-args"], str(build)], check=True)
    (files / "manifest.json").unlink(missing_ok=True)
    require_clean(files)
    args.repo.resolve().mkdir(parents=True, exist_ok=True)
    args.bundle.resolve().parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["flatpak", "build-export", str(args.repo.resolve()), str(build), "stable"], check=True)
    subprocess.run(["flatpak", "build-bundle", str(args.repo.resolve()), str(args.bundle.resolve()), manifest["app-id"], "stable"], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())