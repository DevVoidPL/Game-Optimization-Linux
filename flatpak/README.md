# Build GameOpti with Flatpak

These instructions build the current development source. Public downloads and
release notes are available on [GitHub Releases](https://github.com/DevVoidPL/Game-Optimization-Linux/releases).
See the [roadmap](../ROADMAP.md) for current work and later plans.

Install Flatpak and Flatpak Builder, either from your distribution or as
`org.flatpak.Builder` from Flathub. The manifest uses the Freedesktop 25.08
runtime and SDK. Run the following commands from the repository root.

## Build

With the Flathub Builder application:

```bash
flatpak run --filesystem="$PWD" org.flatpak.Builder \
  --user --force-clean --default-branch=stable \
  --repo=.flatpak-build-repo .flatpak-build-dir \
  flatpak/io.github.DevVoidPL.GameOptimizationLinux.yml
```

A host installation of `flatpak-builder` accepts the same arguments. Dependencies
are pinned in the manifest; the first build downloads their sources. Generated
build directories and repositories stay outside version control.

## Export and install locally

Use the host Flatpak executable for these steps:

```bash
APP_VERSION="$(sed -nE 's/^APP_VERSION[[:space:]]*=[[:space:]]*"([^"[:space:]]+)"[[:space:]]*$/\1/p' src/game_optimization_linux/config.py)"
BUNDLE="dist/Game-Optimization-Linux-${APP_VERSION}-x86_64.flatpak"
mkdir -p dist
flatpak build-bundle .flatpak-build-repo "$BUNDLE" \
  io.github.DevVoidPL.GameOptimizationLinux stable
flatpak install --user --reinstall "$BUNDLE"
flatpak run io.github.DevVoidPL.GameOptimizationLinux --desktop
```

Do not use Builder's `--install` from inside its Flatpak sandbox: installation
must use the host executable. Local installation preserves application data.
Exporting a bundle does not publish a GitHub release.

The manifest includes the maintained runner installer and required application
assets. Optional graphics components are downloaded on demand; local
configuration is not part of the build.

Standalone dependency notices are kept under
`/app/share/licenses/io.github.DevVoidPL.GameOptimizationLinux/`, including
7-Zip, MIT Kerberos and the versioned PySide6 license texts. Python wheel license
files and distribution metadata are retained in `site-packages` as provided by
upstream. This packaging check is not a comprehensive license review of every
Qt module and third-party component bundled in the wheels.

## Couch Edition and Desktop Edition

`io.github.DevVoidPL.GameOptimizationLinux.yml` builds the Couch Edition (complete
application). The Desktop Edition manifest is generated from it and differs only in an
edition marker and the removal of Couch QML and Couch-only icons:

```bash
python3 flatpak/make-desktop-edition-manifest.py
flatpak run org.flatpak.Builder --force-clean --user --install-deps-from=flathub \
  --repo=.flatpak-build-repo-desktop --default-branch=stable .flatpak-build-dir-desktop \
  flatpak/io.github.DevVoidPL.GameOptimizationLinux.desktop-edition.yml
```

Both editions use the same App ID and user data.

Export the two repositories separately, using filenames ending in
`-couch-edition-x86_64.flatpak` and `-desktop-edition-x86_64.flatpak`.
Install only the edition you intend to use. For an existing system-wide
installation, use `flatpak install --system --reinstall` instead of `--user`.
