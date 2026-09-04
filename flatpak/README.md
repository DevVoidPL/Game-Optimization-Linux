# Game Optimization Linux Flatpak

Build the development repository without installing from inside Flatpak Builder:

```bash
flatpak run --filesystem="$PWD" --command=flatpak-builder org.flatpak.Builder \
  --force-clean --repo=.flatpak-build-repo build-flatpak \
  flatpak/io.github.DevVoidPL.GameOptimizationLinux.yml
```

Export the bundle and install it with the host Flatpak executable:

```bash
APP_VERSION="$(sed -nE 's/^APP_VERSION[[:space:]]*=[[:space:]]*"([^"[:space:]]+)"[[:space:]]*$/\1/p' src/game_optimization_linux/config.py)"
BUNDLE="dist/Game-Optimization-Linux-${APP_VERSION}-x86_64.flatpak"
flatpak build-bundle .flatpak-build-repo \
  "$BUNDLE" \
  io.github.DevVoidPL.GameOptimizationLinux stable
flatpak install --user --reinstall \
  "$BUNDLE"
```

Do not pass `--install` to Flatpak Builder running inside its sandbox. That
would export a desktop entry pointing at the Builder sandbox's
`/app/bin/flatpak` instead of the host Flatpak executable.

The manifest pins `py7zr` and all runtime dependencies. Archive extraction is
performed inside the sandbox and never calls a host `7z` executable.
