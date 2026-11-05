# GTK Desktop migration — parallel, incomplete preview

The default frontend remains QML. No QML file is approved for deletion.
The Python backend, controller signatures and Couch interface are unchanged.
The GTK package is adjacent to the `qml` directory, not mixed into its QML
module definitions. This isolates native Python imports from QML packaging.

## Implementation map

Paths below are relative to
`/home/mamanass/Projekty/Game-Optimization-Linux/src/game_optimization_linux`.

| Python file | QML responsibility | Current coverage |
| --- | --- | --- |
| `gtk_frontend/__init__.py` | Qt application bootstrap | Optional GTK entry, Qt/GLib pump, metadata, host bootstrap, shutdown/signals |
| `gtk_frontend/window.py` | `Main.qml`, `Sidebar.qml`, `GamesPage.qml`, `GameListRow.qml`, `ToastHost.qml`, parts of `TasksPage.qml` | Adw.ApplicationWindow, Adw.Flap, searchable Gtk.ListView, context menu, toasts, task status/cancel |
| `gtk_frontend/details.py` | `GameDetailsPage.qml` and its tab tree | Six native pages; basic compression/optimization/MangoHud, read-only advanced statuses, restore listing |
| `gtk_frontend/settings.py` | `SettingsPage.qml` | Adw.PreferencesWindow, theme and language only |
| `gtk_frontend/widgets.py` | controls, `ConfirmDialog.qml`, `I18n.qml` | ActionRow, SwitchRow, SpinRow, ComboRow, confirmation, existing QM catalog lookup |
| `__main__.py` | module entry point, previously absent | `python -m game_optimization_linux` |
| `main.py` (modified) | frontend dispatch | `--gtk`, lazy imports; QML remains default |

The unchanged backend requires QApplication for clipboard and Qt workers.
GTK owns the main loop and display; a GLib timeout dispatches Qt events and
DeferredDelete events through an offscreen QApplication. This is not removal
of the Qt dependency, and requires further worker/shutdown stress testing.

## Desktop QML inventory

Static dependency traversal from Main.qml (including lazy-loaded page names,
component references and singleton references) identifies 63 non-Couch QML
files. This is a conservative source inventory, not runtime coverage proof.
`Main.qml` also loads CouchMain.qml when the interface switches to Couch;
the `couch/` tree is outside this migration and is not modified.

All paths in this inventory start at
`/home/mamanass/Projekty/Game-Optimization-Linux/src/game_optimization_linux/qml/`.

### Root

| File | Purpose |
| --- | --- |
| `Main.qml` | Main window, page loaders, mode switch, close confirmation |
| `Theme.qml` | Theme colors, spacing and typography |
| `I18n.qml` | Translated dynamic messages and status labels |
| `UiIcons.qml` | Icon registry |
| `PopupCoordinator.qml` | Popup ownership and dismissal |

### Pages (`pages/`)

| File | Purpose |
| --- | --- |
| `GamesPage.qml` | Library grid/list, filtering and game actions |
| `GameDetailsPage.qml` | Selected game header, capabilities and tabs |
| `SettingsPage.qml` | Full settings UI |
| `TasksPage.qml` | Task queue/history and controls |
| `UpdatesPage.qml` | Detected game updates |
| `SystemPage.qml` | System and filesystem information |
| `DonatePage.qml` | Support information |

### Per-game components (`pages/details/`)

| File | Purpose |
| --- | --- |
| `OverviewTab.qml` | Game summary |
| `StorageTab.qml` | Compression analysis, planning and verification |
| `OptimizationTab.qml` | Launch optimization settings and components |
| `ScalingEffectsTab.qml` | Upscaling/effects navigation |
| `OptiScalerTab.qml` | OptiScaler configuration |
| `OptiScalerSection.qml` | OptiScaler installation workflow |
| `OptiScalerSummaryCard.qml` | Installation summary |
| `OptiScalerFsrOptions.qml` | FSR options |
| `DlssEnablerPanel.qml` | DLSS Enabler panel |
| `DlssEnablerPanelContent.qml` | DLSS Enabler controls |
| `FrameGenerationPanel.qml` | Frame generation workflow |
| `FrameGenerationSetting.qml` | Individual frame generation setting |
| `PostProcessingPanel.qml` | Post-processing configuration |
| `PostProcessingEffect.qml` | Effect parameters |
| `MangoHudTab.qml` | HUD profile editor |
| `MangoHudPreview.qml` | HUD preview |
| `MangoHudFpsLimitEditor.qml` | FPS limit ownership/editor |
| `MangoHudNumber.qml` | Numeric HUD control |
| `MeasuredOptimizationPanel.qml` | Measurements and baseline/comparison workflow |
| `MeasuredOptimizationText.qml` | Measurement formatting and messages |
| `DeepAnalysisSection.qml` | Deep analysis and cleanup |
| `DeepRecompressionPanel.qml` | Deep recompression workflow |
| `ProtonTweaksSection.qml` | Proton tweaks |
| `CustomLaunchSection.qml` | Custom commands/environment |
| `LaunchEnvironmentDetails.qml` | Effective launch environment |
| `GpuMemoryCard.qml` | GPU memory configuration |

`pages/details/GraphicsTab.qml` also exists, but the traversal found no
reference from the current Desktop tree. It is preserved, not declared dead
and not marked for removal.

### Shared components (`components/`)

| File | Purpose |
| --- | --- |
| `AppButton.qml` | Button |
| `AppComboBox.qml` | Choice control |
| `AppProgressBar.qml` | Progress indicator |
| `AppSlider.qml` | Slider |
| `AppSwitch.qml` | Boolean control |
| `AppTextField.qml` | Text input |
| `Divider.qml` | Separator |
| `EmptyState.qml` | Empty content message |
| `GameArtwork.qml` | Artwork display |
| `GameCover.qml` | Cover image |
| `GameGridCard.qml` | Library grid item |
| `GameListRow.qml` | Library list item |
| `IconButton.qml` | Icon action |
| `LabeledValue.qml` | Label/value presentation |
| `MetricTile.qml` | Metric presentation |
| `PageHeader.qml` | Page heading |
| `SettingRow.qml` | Setting row |
| `Sidebar.qml` | Application navigation |
| `StatusBadge.qml` | Status label |
| `SurfaceCard.qml` | Container styling |
| `TaskCard.qml` | Task presentation/actions |
| `ToastHost.qml` | Notifications |
| `UiIcon.qml` | Icon rendering |

### Dialogs (`dialogs/`)

| File | Purpose |
| --- | --- |
| `ConfirmDialog.qml` | Confirmation |
| `ManualGameDialog.qml` | Manual/local game registration |

## What is implemented versus unverified

Implemented widgets: window, game list/search, game selection, right-click
menu, six per-game pages, preferences, theme binding, toast overlay, warning
banners, task status/cancel. Labels reuse the existing EN/PL/ES QM catalogs.
Unknown/new strings fall back to English; complete GTK i18n and live header/
preferences retranslation remain TODO.

Backend-wired but **not end-to-end validated on real games**:

- Analyze/verify/plan compression and confirm an eligible plan before queuing.
- GameMode and manual target FPS draft, preview and confirmed save.
- MangoHud enabled/font-size draft, preview and confirmed save.
- Component repair/install through the existing launcher API and confirmation.
- Exact disk measurement through the existing guarded API.
- Restore entries supplied by the existing backup service, with confirmation.
  An empty/unavailable backup service does not mean backup functionality exists.

TODO (explicit banners in the relevant pages): full Gamescope configuration,
Proton/custom launch, automatic optimization, OptiScaler installation/removal,
executable pickers and anti-cheat/conflict gates, effects and LUT imports,
MangoHud metrics/colors/graphs/presets and live preview, baseline/comparison
recording and imports, measured candidates, deep analysis/cleanup/recompression,
backup creation/export and tool-specific restore flows, all remaining settings.
Manual game registration, artwork/grid, full task controls/history, updates,
system and donation pages are also not migrated. Advanced statuses shown as
read-only diagnostics are **not** functional equivalents of those editors.

No QML deletion marker is justified yet. Test a complete replacement on real
games before even marking its original component for future removal.

## Dependencies and Flatpak

The optional `gtk` project extra adds PyGObject. GTK4, its introspection data,
libadwaita >= 1.4 and the corresponding native development dependencies must
also exist in the target runtime. PySide6 remains mandatory for the backend.
Adw.Flap and PreferencesWindow are used as requested; newer libadwaita
versions report deprecation warnings. A future migration can use split views
and PreferencesDialog instead.

**No Flatpak manifest was changed.** Existing manifests use the Freedesktop
runtime and do not yet bundle this GTK dependency stack. Their GTK launch is
not supported/validated by this session. Adding an untested GNOME runtime
switch or unverified dependency source checksums would jeopardize QML builds.
TODO: create and build a parallel GTK-capable manifest retaining Qt/backend,
then validate sandbox launch, dialogs, component installation and host bridges.

## Run

From the repository, with Python >= 3.12 and all dependencies installed:

```sh
cd /home/mamanass/Projekty/Game-Optimization-Linux
PYTHONPATH=/home/mamanass/Projekty/Game-Optimization-Linux/src python -m game_optimization_linux --desktop --gtk
```

Without `--gtk`, QML remains the default. Set `GAME_OPTIMIZATION_DEMO=1` for
a non-destructive demonstration (real compression is deliberately unavailable).
Never run as root. Prefer isolated XDG directories for migration testing.

Local validation dependencies were extracted/installed only beneath
`/tmp/gameopti-gtk-runtime`; no system packages were installed. Exact command
for the prepared environment (temporary, not a distributable runtime):

```sh
cd /home/mamanass/Projekty/Game-Optimization-Linux
env \
  PYTHONPATH=/home/mamanass/Projekty/Game-Optimization-Linux/src:/usr/lib/python3/dist-packages \
  GI_TYPELIB_PATH=/tmp/gameopti-gtk-runtime/root/usr/lib/x86_64-linux-gnu/girepository-1.0 \
  LD_LIBRARY_PATH=/tmp/gameopti-gtk-runtime/root/usr/lib/x86_64-linux-gnu \
  XDG_CONFIG_HOME=/tmp/gameopti-gtk-runtime/xdg/config \
  XDG_DATA_HOME=/tmp/gameopti-gtk-runtime/xdg/data \
  XDG_CACHE_HOME=/tmp/gameopti-gtk-runtime/xdg/cache \
  XDG_STATE_HOME=/tmp/gameopti-gtk-runtime/xdg/state \
  GAME_OPTIMIZATION_DEMO=1 GSK_RENDERER=cairo \
  /tmp/gameopti-gtk-runtime/venv/bin/python -m game_optimization_linux --desktop --gtk
```

For local integration tests replace the last line after `python` with
`-m unittest discover -s /home/mamanass/Projekty/Game-Optimization-Linux/tests -p test_gtk_frontend.py -v`.
The new test file is inside the previously excluded local `tests/` tree;
publication cleanup rules intentionally remain untouched. It is not staged
for publication and must be explicitly retained if tests are to be shipped.

## Validation evidence (November 5, 2026)

- Six new tests passed: default QML dispatch, GTK dispatch, module entry,
  native window/list/six tabs/preferences/task-window/search, PL catalog lookup
  with EN/ES loading, and Qt timer delivery inside a GLib main loop.
- Actual `python -m game_optimization_linux --desktop --gtk` launch stayed
  alive for five seconds, then SIGTERM produced exit 143 and the controller
  shutdown log. This is a demo smoke test, not real-game operation validation.
- Combined GTK/config/translations run: 32 passed, 1 skipped, 2 failed.
  Failures: `test_online_optiscaler_and_proton_tweaks_are_translated` and
  `test_interface_sources_and_finished_translations_use_plain_hyphens`.
  The latter flags an em dash in the unchanged GpuMemoryCard.qml. QML and
  existing translation catalogs were not edited to silence these tests.
- Native testing emits requested Adw.Flap/PreferencesWindow deprecations;
  the launch also logs Mesa/EGL and missing optional SDL3 diagnostics.
  A unittest run reports two uncollectable objects at interpreter shutdown;
  shutdown/lifetime stress testing remains TODO.
- Python compilation and git whitespace checks pass. Comparison against HEAD
  confirms QML, controllers, services and models unchanged. No commits/tags,
  Flatpak builds or real-game compression/install/restore operations performed.

The checkout was already at `035e4e774bc3d2bc6b9ac0cd4520ad8539eb4933`
on main and clean when this session inspected it, unlike the older handoff's
uncommitted publication cleanup. Existing commit history was not changed.