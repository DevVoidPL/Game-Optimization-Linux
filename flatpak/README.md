# Flatpak builds

GameOpti 2.3.0 Beta uses the Qt/QML frontend in both editions. Couch contains
Desktop and the full-screen controller interface. Desktop uses the existing
edition gate and leaves out Couch QML/audio and the input-device permission.
Shared icons needed by Desktop remain in the package.

Both builds use App ID `io.github.DevVoidPL.GameOptimizationLinux` and the
Freedesktop 25.08 runtime. They replace each other within one installation.
See the [installation guide](../docs/install-2.3.0-beta.md) before switching
editions or branches. Flathub supplies the runtime, not the GameOpti bundles.

## Build from a clean source export

Install Flatpak Builder and the matching Freedesktop SDK/runtime first. Keep
build directories and repositories outside the source checkout. From its root:

```sh
flatpak run org.flatpak.Builder --user --force-clean --disable-rofiles-fuse   --default-branch=beta --repo=../gameopti-repo-couch ../gameopti-build-couch   flatpak/io.github.DevVoidPL.GameOptimizationLinux.yml

flatpak run org.flatpak.Builder --user --force-clean --disable-rofiles-fuse   --default-branch=beta --repo=../gameopti-repo-desktop ../gameopti-build-desktop   flatpak/io.github.DevVoidPL.GameOptimizationLinux.desktop-edition.yml
```

Export one bundle from each repository:

```sh
flatpak build-bundle ../gameopti-repo-couch   ../GameOpti-2.3.0-beta-couch-x86_64.flatpak io.github.DevVoidPL.GameOptimizationLinux beta
flatpak build-bundle ../gameopti-repo-desktop   ../GameOpti-2.3.0-beta-desktop-x86_64.flatpak io.github.DevVoidPL.GameOptimizationLinux beta
```

`make-desktop-edition-manifest.py` regenerates the Desktop manifest from the
Couch manifest. Its edition changes apply only to Builder’s source copy.

## Host components and permissions

The bundles include a Python-free host runner shim for Steam and a private
7-Zip helper for validated archive handling. Required licenses are retained.
The host provides optional graphics and monitoring tools; they must also be
available to the launcher environment that starts the game.

Steam connection uses the permissions already granted to the application.
It does not add overrides when access to Steam configuration or its runner is
denied. Game libraries outside the declared paths may need an explicit grant.

Use an isolated account or disposable installation to verify a build. Check
startup and shutdown, both interface modes where included, runner arguments,
resources and backup/restore operations before distributing it. Build logs,
test fixtures, debug extensions and local verification reports are not release
assets.
