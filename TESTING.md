# Testing GameOpti

Tests must not read or modify a real Steam library. Use temporary XDG roots and
synthetic game trees for controller, runner, OptiScaler and compression tests.

This guide covers tests run from a source checkout. The current public version
is [GameOpti 2.1 Beta](https://github.com/DevVoidPL/Game-Optimization-Linux/releases/tag/v2.1.0-Beta).
See [ROADMAP.md](ROADMAP.md) for current work and later plans.

## Local checks

Run commands from the repository root with the project interpreter and
temporary XDG roots:

```bash
TEST_ROOT="$(mktemp -d /tmp/gameopti-tests.XXXXXX)"
export XDG_CONFIG_HOME="$TEST_ROOT/config"
export XDG_CACHE_HOME="$TEST_ROOT/cache"
export XDG_STATE_HOME="$TEST_ROOT/state"
export XDG_DATA_HOME="$TEST_ROOT/data"

.venv/bin/python -m pytest
.venv/bin/python -m compileall -q src tests
find src/game_optimization_linux/qml -name '*.qml' -exec .venv/bin/pyside6-qmllint {} \;
appstreamcli validate data/io.github.DevVoidPL.GameOptimizationLinux.metainfo.xml
git diff --check
```

The complete test suite includes unit tests, controller integration tests and
offscreen QML probes. CI also sets temporary XDG roots. Keep these variables in
a dedicated test shell; run the application normally from a separate shell to
use your regular configuration. Individual tests still need synthetic game
trees and must not discover real libraries.

Do not replace `.venv/bin/python -m pytest` with a system `pytest` invocation.
Use the same temporary XDG roots for focused runs.

`tests/conftest.py` creates one `QApplication` for the whole session and defaults
`QT_QPA_PLATFORM` to `offscreen`, so neither needs to be set by hand any more.
The application type matches production, which also builds a `QApplication`.
Setting `QT_QPA_PLATFORM` explicitly still overrides the default.

A single shared application matters: GUI-only statics such as
`QGuiApplication.clipboard()` dereference an uninitialised GUI layer when the
process singleton is a bare `QCoreApplication`, which crashes the interpreter
with SIGSEGV instead of raising. conftest asserts the type up front so a wrong
application fails immediately rather than mid-run.

## Focused groups

Useful focused runs while changing a subsystem:

```bash
.venv/bin/python -m pytest -q \
  tests/test_controller.py \
  tests/test_steam_controller.py \
  tests/test_compression_controller_integration.py

.venv/bin/python -m pytest -q \
  tests/test_optimization_runtime.py \
  tests/test_mangohud.py \
  tests/test_proton_tweaks.py \
  tests/test_optiscaler.py \
  tests/test_optiscaler_online.py

.venv/bin/python -m pytest -q \
  tests/test_host_service.py \
  tests/test_host_bootstrap.py \
  tests/test_btrfs_analysis.py \
  tests/test_compression_engine.py

.venv/bin/python -m pytest -q \
  tests/test_launcher_integration.py \
  tests/test_launcher_native.py \
  tests/test_launcher_providers.py

.venv/bin/python -m pytest -q \
  tests/test_narrator_behavior_contract.py \
  tests/test_narrator_ocr_corpus.py \
  tests/test_narrator_runtime_regressions.py \
  tests/test_narrator_subtitle_episode.py \
  tests/test_narrator_tts_text_filter.py

# UI icons ("Facet" set, see docs/ui-icons.md); regenerate with
# python3 scripts/build-facet-icons.py, verify drift with --check
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q \
  tests/test_ui_icons.py \
  tests/test_ui_icon_presentation.py \
  tests/test_couch_glyphs.py \
  tests/test_couch_design_system.py
```

### Narrator verification in games

The normal Narrator page keeps playback controls, errors and an expandable last
read text. OCR timings, raw recognition and decision history are only created
when the developer UI is enabled for that process:

```bash
GAMEOPTI_DEVELOPER_UI=1 PYTHONPATH=src .venv/bin/python -m game_optimization_linux.main --desktop
flatpak run --env=GAMEOPTI_DEVELOPER_UI=1 io.github.DevVoidPL.GameOptimizationLinux
```

The Flatpak command requires a build containing this UI change. Starting without
the variable restores the normal view. This switch only reveals diagnostics;
it does not change capture, OCR, translation or speech settings.

Text replay tests exercise the decisions made from supplied OCR results; they
do not establish capture, cropping, preprocessing, or Tesseract accuracy in a
real game. The committed OCR corpus contains recorded TSV results, not a full
image/timeline replay dataset.

For a real-game regression, retain the build identity, settings, complete
observation sequence, and corresponding subtitle images. Compare the first
text sent to speech with a manual transcription; record omissions, extra audio
starts for one utterance, meaningful text changes, and time to the first audio.
Evaluate both first-read quality and repetition, rather than only the number
of accepted phrases or passing tests. Sanitize all material before sharing it.

## QML smoke probes

`tests/test_gui_stability.py` launches the QML probes offscreen and checks
Desktop/Couch layouts, navigation, artwork reuse, dialogs, tasks, Updates and
the optimization editor. Run it directly with:

```bash
.venv/bin/python -m pytest -q tests/test_gui_stability.py
```

A single probe can be inspected as JSON, for example:

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=src \
  .venv/bin/python tests/qml_runtime_probe.py optimization
```

## Flatpak checks

Builds use `flatpak/io.github.DevVoidPL.GameOptimizationLinux.yml`. Prefer
focused source checks during development; use a development Flatpak for
sandbox/host verification and export a complete bundle when needed for release
validation. A locally built bundle is not a public release.

After building a bundle, install it into the user installation and verify both
modes:

```bash
APP_VERSION="$(sed -nE 's/^APP_VERSION[[:space:]]*=[[:space:]]*"([^"[:space:]]+)"[[:space:]]*$/\1/p' src/game_optimization_linux/config.py)"
BUNDLE="./dist/Game-Optimization-Linux-${APP_VERSION}-x86_64.flatpak"
flatpak install --user --reinstall "$BUNDLE"
flatpak run io.github.DevVoidPL.GameOptimizationLinux --desktop
```

Then switch to Couch Mode with F11 or the saved controller-mode setting. There
is no separate `--couch` command-line option.

Also verify runner bootstrap and a plan-only command:

```bash
flatpak run --command=game-optimization-install-runner \
  io.github.DevVoidPL.GameOptimizationLinux
~/.local/share/game-optimization-linux/bin/game-optimization-run \
  --appid 480 --plan-only -- true
```

For OptiScaler, use a synthetic game directory. The development probe creates
its own executable, conflict file and archive, then checks install, removal and
restoration without touching a Steam game. Tests are intentionally excluded
from release bundles, so `tests/flatpak_optiscaler_probe.py` is run from a
development Flatpak build rather than expected under `/app` in a release.

## Optional Btrfs integration

Real Btrfs tests are opt-in. Point the variable only at an expendable directory
on Btrfs, never at `steamapps` or another real library:

```bash
GAME_OPTIMIZATION_BTRFS_TEST_ROOT=/path/to/safe/btrfs/test-root \
  .venv/bin/python -m pytest -q \
    tests/test_btrfs_compression_integration.py \
    tests/test_btrfs_shared_extents.py
```

Without the variable these write-capable tests are skipped. Exact `compsize`
measurement may also be unavailable when the optional privileged host
component is not installed; the expected result is an explicit unavailable
state, not a fabricated measurement.

## Reports

Post-processing checks use disposable folders, never a real Steam library:
`.venv/bin/python -m pytest -q tests/test_post_processing.py tests/test_post_processing_ui.py`.
See [backend compatibility and in-game test procedures](docs/post-processing.md).
An installation/hash check, a loaded module and a visibly verified effect are separate results.

When reporting a failure, include the application version, distribution,
Desktop/Couch mode, relevant test output and whether Steam is native or
Flatpak. Remove usernames, private paths and the game list from logs before
sharing them.
