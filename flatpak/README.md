# Game Optimization Linux Flatpak

Build and install the development package without root:

```bash
flatpak run --command=flatpak-builder org.flatpak.Builder \
  --user --install --force-clean build-flatpak \
  flatpak/io.github.DevVoidPL.GameOptimizationLinux.yml
```

The manifest pins `py7zr` and all runtime dependencies. Archive extraction is
performed inside the sandbox and never calls a host `7z` executable.
