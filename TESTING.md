# Testing Game Optimization Linux

Tests must not read or modify a real Steam library. Use temporary XDG roots and
synthetic game trees for controller, runner, OptiScaler and compression tests.

## Local checks

Run commands from the repository root with the project interpreter:

```bash
.venv/bin/python -m pytest
.venv/bin/python -m compileall -q src tests
find src/game_optimization_linux/qml -name '*.qml' -exec qmllint {} +
appstreamcli validate data/io.github.DevVoidPL.GameOptimizationLinux.metainfo.xml
```

The complete test suite includes unit tests, controller integration tests and
offscreen QML probes. It isolates configuration, cache and state directories;
do not replace `.venv/bin/python -m pytest` with a system `pytest` invocation.

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
```

## QML smoke probes

`tests/test_gui_stability.py` launches the QML probes offscreen and checks
Desktop/Couch layouts, navigation, artwork reuse, dialogs, tasks, Updates and
the optimization editor. Run it directly with:

```bash
QT_QPA_PLATFORM=offscreen \
  .venv/bin/python -m pytest -q tests/test_gui_stability.py
```

A single probe can be inspected as JSON, for example:

```bash
QT_QPA_PLATFORM=offscreen PYTHONPATH=src \
  .venv/bin/python tests/qml_runtime_probe.py optimization
```

## Flatpak checks

Builds use `flatpak/io.github.DevVoidPL.GameOptimizationLinux.yml`. After
building a bundle, install it into the user installation and verify both modes:

```bash
flatpak install --user --reinstall ./dist/Game-Optimization-Linux-0.1.2-alpha-x86_64.flatpak
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

When reporting a failure, include the application version, distribution,
Desktop/Couch mode, relevant test output and whether Steam is native or
Flatpak. Remove usernames, private paths and the game list from logs before
sharing them.
