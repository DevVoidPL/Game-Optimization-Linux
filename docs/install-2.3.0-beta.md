# Install GameOpti 2.3.0 Beta

Download a package and `SHA256SUMS` from the `v2.3.0-beta` GitHub Release.
Check the chosen file before installing:

```sh
grep '  GameOpti-2.3.0-beta-desktop-x86_64.flatpak$' SHA256SUMS | sha256sum -c -
```

Use the exact filename of the package you downloaded. Run
`sha256sum -c SHA256SUMS` if you have all the listed files.

## Flatpak

**For public beta testing, use a separate Linux test account.** Branch `beta`
does not isolate data from `stable`: both use `~/.var/app/` for the same App ID.
`FLATPAK_USER_DIR` isolates deployments, not that data. Never run both versions
concurrently or use `uninstall --delete-data` to remove just the beta.
Back up your application data before updating; a beta is not an isolated profile.

The runtime is Freedesktop 25.08 (Flathub). Choose exactly one edition and keep
the same installation scope as your existing package. For an existing system
installation use `--system` instead of `--user`; do not create a duplicate user
installation. Do not use `uninstall --delete-data` when switching editions.

```sh
flatpak install --user --reinstall ./GameOpti-2.3.0-beta-couch-x86_64.flatpak
# OR, Desktop only:
flatpak install --user --reinstall ./GameOpti-2.3.0-beta-desktop-x86_64.flatpak
flatpak run --user io.github.DevVoidPL.GameOptimizationLinux//beta --desktop
```

Both variants share the App ID and profiles. These bundles use branch `beta`.
Installing beta makes it current; restore `flatpak make-current --user
io.github.DevVoidPL.GameOptimizationLinux stable` in the existing stable scope
if you want the stable menu launcher. Use `--system` for an existing system scope.
Switching Desktop/Couch requires replacing the bundle, not make-current.
Updates follow the configured origin: a later update from the wrong edition's
repository can replace the selected edition. Initial beta bundles do not promise
an automatic update channel. Switching back to an older build is not a guarantee
of profile compatibility; retain your pre-update backup.
Existing permissions cover home, /mnt, /media, /run/media and specific launcher
metadata. Unusual game paths may require an explicit Flatpak filesystem grant.

## AppImage

```sh
chmod +x ./GameOpti-2.3.0-beta-x86_64.AppImage
./GameOpti-2.3.0-beta-x86_64.AppImage
```

No graphics environment variable is required on the tested configuration.
The image includes both modes. Keep it at a stable path after configuring
Steam Launch Options; those reference the image itself. After moving/replacing
the file, regenerate the relevant launch command if its path changed.
It does not automatically install a menu shortcut into your profile. Desktop
integration tools can use its included desktop entry and icon. For diagnostics,
run `QSG_INFO=1 ./GameOpti-2.3.0-beta-x86_64.AppImage` and inspect the XDG state log
under `game-optimization-linux/logs/`. Software rendering remains an explicit
workaround, not the default. The AppImage baseline was tested with glibc
2.35 (Ubuntu 22.04 userspace); its highest imported GLIBC symbol is 2.34, which
is not a claim of testing a 2.34 distribution. Host graphics/C++ libraries are
needed (GLIBCXX 3.4.29 / CXXABI 1.3.13); glibc alone is not a compatibility promise.

## Arch / AUR

AUR publication is a later step. The release source archive is retained for a
reviewed native package recipe; it is not an installed package or an AUR listing.
No AUR installation command is available as part of this release.