# GameOpti 2.3.0 Beta

This is a pre-release. Back up your profiles before updating. Compatibility
depends on the game, launcher, GPU, drivers and installed tools.

## Changes since 2.2.0

- **AppImage on Wayland:** fixed a reproduced startup crash caused by bundled
  Wayland libraries conflicting with the host graphics stack. The image uses
  the host graphics, Wayland and C++ runtime libraries. Hardware rendering
  remains the default.
- **OptiScaler:** simplified the per-game panel with status, a combined
  install/update/repair action, progress, cancellation and removal. Preflight
  checks, original-file backups, conflict confirmation and post-install
  verification remain in place. Status now accounts for files already present
  in the game directory, including installations made by another package.
- **Couch:** restored missing controls for frame generation, GPU memory,
  post-processing and Deep Compression, with controller navigation and an
  executable picker. Desktop packaging uses the maintained Qt/QML interface.
- **Steam Launch Options:** added a shared Steam connection panel in Desktop
  and Couch with explicit confirmation, graceful Steam shutdown, backup and
  Restore. Ordinary arguments and known wrappers are preserved; unknown or
  ambiguous commands are left unchanged. Manually entered commands pointing to
  a recognized GameOpti AppImage are detected without requiring a panel record.
- **Heroic and Lutris:** improved native/Flatpak discovery and launcher-profile
  handling, including backup and restore checks. Tool availability still
  depends on the environment in which each launcher runs the game.
- **Game artwork:** improved resolution- and aspect-aware local selection,
  stopped using small icons as large covers, and added a validated Steam
  artwork cache for missing or undersized images. Desktop headers and Couch
  cards use the corrected selection.
- **GUI and DLSS Enabler:** removed the duplicate detection-only DLSS Enabler
  entry from the OptiScaler selector. The separate DLSS Enabler panel remains.
  Adjusted artwork layout and guarded callbacks during UI teardown.
- **Update history:** Couch can clear finished results and errors after
  confirmation while retaining active work and game files.
- Removed the financial-support page, its navigation, assets and related translations.

## Choose a download

| File | Use |
| --- | --- |
| `GameOpti-2.3.0-beta-desktop-x86_64.flatpak` | Classic Desktop interface for mouse and keyboard. |
| `GameOpti-2.3.0-beta-couch-x86_64.flatpak` | Desktop plus full-screen Couch mode and controller controls. |
| `GameOpti-2.3.0-beta-x86_64.AppImage` | Portable build with both interface modes. |
| `SHA256SUMS` | Checksums for the release downloads. |
| `gameopti-2.3.0beta.tar.gz` | Release source archive retained for the later native Arch/AUR recipe. |

The two Flatpaks share App ID `io.github.DevVoidPL.GameOptimizationLinux` and
the same profiles. They replace each other within one installation, rather
than installing as two independent apps. Keep your existing user or system
installation scope. Beta and stable branches also share application data.
Use a separate Linux account if you need isolated testing; do not use
`--delete-data` to remove only the beta.

### Install a Flatpak

Choose one bundle. For a per-user installation:

```sh
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install --user --reinstall ./GameOpti-2.3.0-beta-desktop-x86_64.flatpak
```

For Couch, use `GameOpti-2.3.0-beta-couch-x86_64.flatpak` instead. If your
existing installation is system-wide, use `--system` instead of `--user`.

```sh
flatpak run --user io.github.DevVoidPL.GameOptimizationLinux//beta --desktop
```

### Run the AppImage

```sh
chmod +x ./GameOpti-2.3.0-beta-x86_64.AppImage
./GameOpti-2.3.0-beta-x86_64.AppImage
```

The tested baseline is Ubuntu 22.04 userspace with **glibc 2.35**. The image is
intended for glibc 2.35+ systems with compatible host graphics and C++ libraries.
It is not guaranteed to run on every distribution. Keep its path stable if
Steam Launch Options point to it; moving or renaming it requires an explicit
update of that command. An older existing image can still be recognized, but
Steam will continue to launch that older file until you change the path.

### Verify a download

Save `SHA256SUMS` alongside your downloaded package, then check that file:

```sh
grep '  GameOpti-2.3.0-beta-desktop-x86_64.flatpak$' SHA256SUMS | sha256sum -c -
```

Substitute the exact Couch or AppImage filename for the package you chose.
If you downloaded all listed assets, run `sha256sum -c SHA256SUMS` instead.

## Beta limitations

- Testing does not cover every GPU, distribution, display setup or game
  configuration. The project owner checked AppImage, Flatpak, OptiScaler,
  Couch, Steam connection and artwork manually. Automated regression and
  runtime smoke tests supplement those checks; they do not establish universal
  compatibility or an FPS improvement.
- Wayland and X11/XWayland startup were checked. A separate native Xorg session,
  all controller models and all HiDPI/TV configurations have not been qualified.
- Graphics injection tools can conflict with individual games or anti-cheat.
  GameOpti does not bypass anti-cheat and does not guarantee backend compatibility.
- Steam setup requires an identifiable account and access to the configuration
  and runner. Flatpak permission failures are reported without adding overrides.
  Apply may close Steam after confirmation and leaves it closed afterwards.
  Restore requires a valid record of the previous value and stops on conflicts.
- AppImage recognition checks known entry-point contents, not just the filename.
  It is not a signature of the whole image. Unknown future runner builds or
  inaccessible files may need manual configuration.
- Flatpak and AppImage use different XDG profile locations. Profiles, OptiScaler
  manifests and backups are not automatically merged between them.
- Some Spanish translations remain incomplete. No automatic update channel is
  promised for these standalone beta bundles.

AUR publication comes later. No AUR package installation command is provided
with this release. The earlier `v2.2.0-beta` release remains available.