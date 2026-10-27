# Build GameOpti with Flatpak

These instructions build the current development source. Public downloads and
release notes are available on [GitHub Releases](https://github.com/DevVoidPL/Game-Optimization-Linux/releases).
See the [project status](../ROADMAP.md) for the Beta 2.0 schedule.

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
assets. Optional graphics components and Narrator models are downloaded on
demand; private voice files and local configuration are not part of the build.

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

Both editions bundle the Voidek voice from `flatpak/bundled/` (not tracked in git;
the manifest pins its SHA-256). Both use the same App ID and user data.
