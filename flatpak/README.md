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

## Couch Edition and Desktop Edition

`io.github.DevVoidPL.GameOptimizationLinux.yml` builds the Couch Edition (complete
application); its build is unchanged. Desktop uses a separate, compiled GTK4 /
libadwaita entry point and the GNOME 49 runtime/SDK. Its generated manifest
compiles Nuitka inside the GNOME SDK sandbox, using the pinned Python dependencies
and a `build-tools` module immediately before the application. Nuitka, ordered-set,
zstandard and patchelf are downloaded as checksum-pinned sources, then installed
offline using pip. No host-built binary from `dist/nuitka` is used:

```bash
python3 flatpak/make-desktop-edition-manifest.py
flatpak run --filesystem="$PWD" org.flatpak.Builder \
  --force-clean --user --install-deps-from=flathub --build-only \
  .flatpak-build-dir-desktop \
  flatpak/io.github.DevVoidPL.GameOptimizationLinux.desktop-edition.yml
```

Both editions use the same App ID and user data.

Export the two repositories separately, using filenames ending in
`-couch-edition-x86_64.flatpak` and `-desktop-edition-x86_64.flatpak`.
Install only the edition you intend to use. For an existing system-wide
installation, use `flatpak install --system --reinstall` instead of `--user`.

### Desktop release policy and export

Desktop invokes `python3 flatpak/desktop_release.py build --prefix=${FLATPAK_DEST}`
inside the sandbox. It compiles the GTK entry point and backend, verifies the
compilation report and standalone payload, and runs a compiled import/resource
self-test before installation. The compiled runner launcher is installed too.
Couch remains unchanged. Compiler packages and Python dependency sources are
removed from `/app` after compilation; patchelf is removed before the final audit.
Before compilation, `ldconfig -X -i` creates `/etc/ld.so.cache` from `/app/lib`
and SDK `/usr/lib` paths inside the disposable build sandbox. This supports
Nuitka's `ldconfig -p` dependency scan without borrowing the host cache or
changing library symlinks. GCC is provided by the SDK; no apt-get is used.
Cache creation and compilation must be a single `build-commands` entry joined
with `&&`: Builder recreates `/etc` between separate commands.

Only image assets and compiled `.qm` catalogs are included through an explicit
resource allowlist. Do not replace this with recursive copying of `data`,
`libexec`, or package data. The host runner shim and its installer remain shell
scripts by design; they are not Python application sources. The optional host
measurement helper is not bundled by this manifest.

The packaging build checks only `/app`, never runtime `/usr`. Because
`--build-only` skips Builder's cleanup phase, the Desktop module explicitly
removes `/app/include`, `/app/lib/debug` and `/app/lib/*.a` before auditing.
These are build artifacts installed by native dependency modules, not runtime
files; they are removed rather than exempted from the audit. Runtime shared
libraries and required licenses remain subject to the normal audit.
The standalone directory is audited before accepting the onefile binary, and
the XML report must mark every application Python module as compiled. Compiler trees, pip metadata,
tests, Python/bytecode, QML, translation sources and known development/prompt
markers are rejected. Required licenses are preserved. Neither pattern matching
nor native compilation can prove the absence of all possible provenance strings
or prevent reverse engineering. This is source-exclusion, not cryptographic
protection or a claim that logic cannot be recovered.

Build-only output and XML reports stay outside `/app`. Retain the standalone
directory from the sandbox compiler build for its payload audit.
Delete the isolated build workspace after retaining any reports privately.
Do not distribute a debug/sources extension or compiler report with the release.

Finalize and export manually after `--build-only`, rather than exporting through
Builder (which can include its manifest). These commands need host Python with
PyYAML, Flatpak, and installed GNOME 49 runtime/SDK dependencies:

```bash
python3 flatpak/export-desktop.py \
  --build-dir "$PWD/.flatpak-build-dir-desktop" \
  --repo "$PWD/.flatpak-build-repo-desktop" \
  --bundle "$PWD/dist/GameOpti-2.2.0-desktop-edition-x86_64.flatpak"
```

The exporter finalizes a fresh build, removes only Builder's `files/manifest.json`,
re-audits the finished files and invokes `flatpak build-export` directly. Flatpak
runtime metadata, AppStream metadata and legally required license notices are
intentionally retained: they are not build recipes. Validate a compiled bundle
in a disposable installation (GTK startup/shutdown, runner, resources, real-game
fixtures) before publishing. Unit tests alone do not qualify it as release-ready:

```bash
python3 -m pytest flatpak/test_desktop_release.py -q
```

### Local verification, 2026-11-05

The sandbox-compiled Desktop build, export, local user installation and installed
`--release-self-test` completed with exit code 0. The standalone payload/report
checks and exported `/app` audit passed. However, the X11 GUI smoke test did not
pass: the process stayed running but its window remained 1x1 and `IsUnMapped`,
both with the default renderer and with `GSK_RENDERER=cairo`. This bundle is not
qualified as a working GUI release. No crash traceback was observed; the reason
for the unmapped window is not established. Test processes were stopped.
Logs from this run are local at `/tmp/gameopti-sandbox-cache-lfStS6DY/`.
