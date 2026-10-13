# Architecture

Game Optimization Linux uses QML for presentation and Python for application
state, platform integration and file operations.

This describes the development checkout, which may include unfinished work
beyond the public 1.8 Alpha. See [development status](../ROADMAP.md); the
announced 2.0 Beta is not yet available for download.

## Application layers

- `qml/` contains the Desktop and Couch shells, pages and shared controls. QML
  calls only the properties, signals and slots exposed by `AppController`.
- `AppController` is the QObject facade. It owns the QML-visible snapshots,
  Qt timers and signal delivery, then delegates domain work to controllers in
  `controllers/`.
- Domain controllers group library scanning, compression/tasks, Updates,
  MangoHud, OptiScaler, optimization/Proton, settings and system presentation.
  Public QML method names remain on the facade so the UI API stays stable.
- `services/` contains persistence, planning, background tasks and integrations
  such as OptiScaler, MangoHud, Proton Tweaks and Steam update tracking.
- `providers/` read Steam/Linux state or perform the guarded Btrfs operations.
  Models in `models/` carry typed state between providers, services and the
  controllers.

Background workers do not mutate QML state directly. Their results are polled
or delivered to the facade, which updates its snapshots and emits Qt signals on
the application thread.

## Per-game launch flow

Per-game files live below the application's XDG configuration directory. The
optimization profile, MangoHud profile, Proton Tweaks and OptiScaler state are
loaded for one Steam AppID and merged into a `LaunchPlan`:

```text
per-game profiles -> OptimizationLaunchPlanner -> LaunchPlan
                                             -> game-optimization-run
                                             -> Steam game process
```

The plan keeps the executable, arguments, wrappers and environment separate.
The runner executes argv directly and writes a short launch report. Steam keeps
one stable Launch Options command; profile changes do not edit Steam VDF files.

In the development checkout, Heroic/Lutris profiles can be applied through
managed environment/wrapper entries in the selected game's launcher
configuration, with a backup and restore workflow. This is a separate path
from the Steam runner; measured FPS sessions remain tied to the Steam runner.
Custom/Manual launch configuration does not use the full optimization pipeline.

## Narrator flow

The local Narrator uses portal-authorized screen capture, PipeWire/GStreamer,
a selected subtitle region, Tesseract OCR, text filtering and consensus,
subtitle-episode deduplication, optional English-to-Polish translation, and
Piper speech output. The Polish subtitle mode bypasses translation.

OCR quality and episode decisions are separate verification targets: avoiding
a second reading does not establish that the first reading was accurate.
Component details are in [narrator-components.md](narrator-components.md),
and testing guidance is in [../TESTING.md](../TESTING.md).

## Flatpak and host integration

The Flatpak packages the Python/Qt application and the Btrfs command used by
the guarded compression provider. The user-level runner is copied from the
Flatpak into the application's XDG data directory so Steam can invoke it on the
host.

Host tool detection uses a narrow host bridge with predefined operations. It
does not expose a general command or shell interface. Optional exact
`compsize` measurement may use a separately installed privileged component;
when unavailable, the application reports that limitation without blocking
the remaining Btrfs workflow.

The Flatpak manifest, desktop entry, AppStream metadata and icons share the
application ID `io.github.DevVoidPL.GameOptimizationLinux`.
