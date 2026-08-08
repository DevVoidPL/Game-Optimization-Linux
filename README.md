# Game Optimization Linux

Game Optimization Linux is an open-source Linux gaming toolkit for per-game
configuration, launch optimization and storage management. It brings common
gaming tools into one interface and keeps each game's settings separate.

The project does not replace Steam, Heroic Games Launcher or Lutris. Steam is
currently the supported library and launch source; support for more launchers
is planned.

Game Optimization Linux is in alpha / early beta development. Profiles and
safety checks are usable, but releases still need testing across more Linux
distributions and hardware configurations.

## What works today

- discovery of native and Flatpak Steam installations, libraries and installed
  games;
- cached entries for temporarily disconnected libraries, with local Steam
  artwork and separate portrait/header selection;
- Desktop Mode and a controller-oriented Couch Mode with SDL3 hotplug support;
- English, Polish and Spanish interfaces;
- per-game optimization profiles and a stable Game Optimization Runner command;
- GameMode and Gamescope detection and launch-plan integration;
- per-game MangoHud profiles, including executable-specific configuration;
- per-game Proton Tweaks with a preview of the final environment;
- online OptiScaler release discovery from the official OptiScaler GitHub
  repository, verified cache, executable selection and install/update/remove;
- Btrfs compression analysis, guarded recompression, task progress and history;
- mounted-filesystem and system information;
- local Steam manifest/update tracking.

GameMode, Gamescope and MangoHud remain optional. A missing tool disables only
the related part of a launch plan. The final command and environment are shown
before use.

## Flatpak installation

Flatpak is the primary distribution format. Download the current `.flatpak`
bundle from GitHub Releases for this repository, then install it with:

```bash
flatpak install --user ./Game-Optimization-Linux-0.1.2-alpha-x86_64.flatpak
flatpak run io.github.DevVoidPL.GameOptimizationLinux
```

The Flatpak is designed to avoid distribution-specific filesystem layouts and
is being tested on additional distributions. This is not yet a claim that
every host integration works on every distribution. Optional host tools are
detected independently and their absence does not prevent the application from
starting.

To use saved launch profiles from Steam, install the small user-level runner
shipped inside the Flatpak:

```bash
flatpak run --command=game-optimization-install-runner \
  io.github.DevVoidPL.GameOptimizationLinux
```

No `sudo` is used. The runner is copied to
`~/.local/share/game-optimization-linux/bin/`.

## Per-game launch profiles

The Optimization page combines supported settings into one deterministic
`LaunchPlan`:

- GameMode and Gamescope wrappers;
- the selected display, render/output resolution and FPS limit;
- MangoHud activation and per-executable configuration;
- Proton Tweaks;
- OptiScaler's required `WINEDLLOVERRIDES`.

Steam Launch Options contain one stable command. For example:

```text
"/home/USER/.local/share/game-optimization-linux/bin/game-optimization-run" --appid 480 -- %command%
```

Changing a profile does not require replacing that command. Game Optimization
Linux does not automatically edit Steam VDF files; the user copies the shown
command into Steam once.

## OptiScaler and Proton

The OptiScaler integration reads stable release metadata only from the
official `optiscaler/OptiScaler` GitHub repository. Downloads are cached in the
application's XDG cache and validated before extraction. Installation is
performed next to the chosen game executable, which can be selected manually
when detection is ambiguous. The application records exactly which files it
creates or replaces so update, repair and removal do not delete unrelated
mods.

Local `.7z` and `.zip` archives remain available as an advanced fallback. Files
inside an archive are never executed by Game Optimization Linux.

Proton Tweaks are stored per Steam AppID and are disabled by default unless the
user enables them. Compatibility and experimental variables are labelled in
the interface and merged with the rest of the runner environment.

OptiScaler and DLL overrides can trigger anti-cheat systems or violate a game's
online rules. Do not install them for online or anti-cheat protected games
unless you have checked the game's policy and accept the risk. Detection is a
warning aid, not a guarantee of compatibility.

## Btrfs compression

Game compression currently works only on Btrfs. ext4 and other filesystems do
not support this workflow.

Before recompression the application checks the installation path, mount,
available space, running processes, Steam state and shared extents. Analysis is
read-only. A write plan must pass the safety checks before the Btrfs provider
sets the directory compression property and recompresses the verified files.
Tasks can be cancelled and the result is measured again after the operation.

Savings are not fixed. They depend on the exact game assets and on data that is
already compressed. Some games save a substantial amount of space, while
others save only a few percent or almost nothing. Game Optimization Linux shows
the estimate before writing and warns when the likely benefit is too small.
It never presents a constant percentage as a guarantee.

`compsize` is used for an exact view of Btrfs compressed extents when the host
measurement component is available. Without it, compression can still run,
but the precise compression ratio and reclaimed-space claim are shown as
unavailable rather than inferred from `du` or scanner data.

Shared extents and reflinks require special care because defragmentation can
break sharing and increase disk use. Unsafe or uncertain plans are blocked;
snapshots are never removed automatically.

## Data and safety

Configuration follows the XDG base directory specification:

- profiles and settings: `$XDG_CONFIG_HOME/game-optimization-linux/`;
- caches: `$XDG_CACHE_HOME/game-optimization-linux/`;
- task and operation history: `$XDG_STATE_HOME/game-optimization-linux/`;
- managed OptiScaler data and the host runner:
  `$XDG_DATA_HOME/game-optimization-linux/`.

System commands are built as argument lists. The application does not use
`shell=True`, does not run as root and does not silently modify Steam's VDF
configuration.

## Planned work

The following items are planned and are not current features:

- Deep Optimize;
- a local AI narrator / voiceover workflow;
- texture enhancement and AI Remaster;
- broader launcher and library support;
- continued compatibility work across Linux distributions.

## Community

A Discord server is being prepared for news, development updates, feature
previews, feedback and community testing. The link will be added after the
server opens publicly.

Developer / Discord: `xvoiddeveloper`

## Development

Python 3.12 or newer is required for a source checkout. Create a virtual
environment and install the project with its test dependencies:

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

Run the native development build with:

```bash
.venv/bin/game-optimization-linux --desktop
```

The usual checks are:

```bash
.venv/bin/python -m pytest
.venv/bin/python -m compileall -q src tests
find src/game_optimization_linux/qml -name '*.qml' -exec qmllint {} +
```

Tests use temporary XDG directories and synthetic Steam/game trees. Real Btrfs
integration tests are opt-in and must never point at a real game library. See
[TESTING.md](TESTING.md) for the test matrix and
[docs/architecture.md](docs/architecture.md) for the code layout.

## License

Game Optimization Linux is distributed under the MIT License.
