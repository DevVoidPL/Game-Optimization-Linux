# AppImage packaging

The 2.2.0 release shipped an AppDir but no tracked build script. This builder
retains that AppRun/private-loader model; it uses fresh, checksum-pinned Python
and the same dependency wheels as the maintained Flatpak manifest. Application
sources always come from this checkout, never from a previous binary release.

`build.py --work ABSOLUTE_NEW_DIRECTORY --cache ABSOLUTE_CACHE --library-root
ABSOLUTE_BASELINE_LIB_DIRECTORY --output ABSOLUTE_APPIMAGE` assembles Couch
(both modes); `--edition desktop` uses the existing edition gate. Use a supported
distribution baseline and record its identity, not a rolling developer host.
Requires Python, PyYAML, make/C++ compiler, binutils, file and network for verified downloads. The mutable
appimagetool URL is checksum-pinned and fails closed if upstream replaces it.
The AppImage runtime is separately downloaded and checksum-verified rather than
allowing appimagetool to fetch an unpinned runtime implicitly. Build all native
helpers in the same baseline as the bundled libraries.

The host provides glibc, C++ runtime, Wayland, libudev, fontconfig and GPU driver
libraries. Never bundle older copies that interpose on a newer host Mesa.
`--library-path` applies to the application loader only, not child processes.
No unconditional software backend or silent restart is used. Qt/application
diagnostics are written to the normal XDG state log. For graphics diagnostics,
use `QSG_INFO=1`; `QT_QUICK_BACKEND=software` remains an explicit user workaround.

The minimum glibc depends on ALL shipped ELF files, not just Python. Inspect
`assembly-report.json` and ELF version requirements before distribution. Test
Wayland and X11 on the minimum baseline and newer distributions. AppImage is
not a promise of compatibility with every Linux distribution.

The latest local candidate uses Ubuntu 22.04 userspace (glibc 2.35): payload
imports top out at GLIBC 2.34, GLIBCXX 3.4.29 and CXXABI 1.3.13. The tested
minimum is **2.35**, not an untested 2.34 distribution. `--max-glibc 2.35` is the
builder default; assembly fails before packaging on a newer requirement,
unresolved bundled library or absent Couch SDL3. Both payload and runtime are
audited. Keep the ABI JSON with the candidate.

Run `prepare-sdl.sh` inside the disposable baseline with the pinned SDL 3.2.30
archive, a new absolute build directory and `/usr` as the install prefix there.
It builds controller support only; Qt provides application graphics/audio.
Never run that baseline installation script against the developer host `/usr`.

Qt/PySide retain upstream LGPL/GPL/commercial terms and license/notice files.
Python retains its upstream licenses. GameOpti is MIT. The image contains
replaceable shared libraries and Python/QML source; no source-exclusion policy.