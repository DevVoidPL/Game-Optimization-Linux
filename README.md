
# Game Optimization Linux

**Game Optimization Linux** is an open-source Linux gaming toolkit focused on per-game optimization, performance analysis, launch configuration, storage management and experimental accessibility features.

It brings multiple Linux gaming technologies into one interface, while also providing its own game-analysis, performance-measurement and optimization systems.

The project does not replace Steam, Heroic Games Launcher or Lutris. Steam is currently the primary supported library and launch source; support for additional launchers is planned.

Game Optimization Linux is currently in **Alpha** development. Many features already work with real games, but compatibility still varies between games, Proton versions, desktop environments and hardware.

> **Current version: 1.6 Alpha**

---

## What works today

- discovery of native and Flatpak Steam installations, libraries and installed games;
- cached entries for temporarily disconnected libraries, with local Steam artwork;
- Desktop Mode and controller-oriented Couch Mode with SDL3 support;
- English, Polish and Spanish interfaces;
- per-game optimization profiles;
- Game Optimization Runner with one stable Steam Launch Option;
- GameMode and Gamescope integration;
- graphical per-game MangoHud configuration;
- real-game MangoHud performance recording;
- experimental performance baseline sessions;
- Game Analyzer with hardware, runtime and executable information;
- CPU/GPU bottleneck analysis;
- RAM and VRAM pressure analysis;
- FPS-limit and frame-pacing detection;
- experimental Automatic Optimization;
- per-game Proton Tweaks;
- OptiScaler discovery, installation, update and removal;
- Btrfs game compression analysis and recompression;
- exact Btrfs compression measurements with `compsize` where available;
- mounted-filesystem and system information;
- local Steam manifest/update tracking;
- experimental local Polish game narrator.

GameMode, Gamescope, MangoHud and other integrations remain optional. Missing tools disable only the related functionality instead of preventing the application from starting.

---

## Game Analyzer and performance analysis

Game Optimization Linux can analyze an installed game together with the current system and runtime environment.

Depending on the game and available information, the analyzer can inspect or detect:

- game executable;
- executable architecture;
- game engine;
- graphics API;
- Steam / Proton runtime information;
- CPU;
- GPU;
- RAM;
- VRAM;
- display resolution and refresh rate;
- game installation and filesystem information.

Current experimental engine detection includes engines such as:

- Unreal Engine;
- Unity;
- REDengine.

Detection is intentionally conservative. If reliable information is unavailable, the application should prefer returning `Unknown` instead of inventing a result.

### Real gameplay measurements

Game Optimization Linux can record a performance baseline using MangoHud.

The recorded data can be used to detect:

- GPU bottlenecks;
- CPU bottlenecks;
- VRAM pressure;
- RAM pressure;
- frame-pacing problems;
- FPS limits;
- balanced workloads;
- insufficient measurement data.

The goal is to base optimization decisions on actual gameplay measurements instead of static hardware assumptions.

Performance analysis is still experimental and needs testing with more games and hardware configurations.

---

## Experimental Automatic Optimization

Automatic Optimization is currently being developed around the following workflow:

`Analyze Game -> Measure -> Detect Bottleneck -> Recommend -> Preview -> Apply -> Measure Again -> Keep / Revert`

The current implementation is still an early foundation, but it can already combine game information, hardware information and real MangoHud measurements when evaluating optimization candidates.

Game Optimization Linux deliberately avoids:

- fake FPS predictions;
- generic "RAM booster" behavior;
- blindly clearing system caches;
- random global sysctl collections;
- applying unknown engine variables without evidence;
- recommending changes only because they are technically possible.

If there is not enough reliable information, the application should prefer making no recommendation instead of applying a placebo optimization.

Not every detected bottleneck currently has an automatic optimization available.

---

## Experimental Polish Narrator

Game Optimization Linux includes an experimental local Polish narrator for games with English subtitles.

The current pipeline is:

`Game capture -> OCR -> English to Polish translation -> Polish TTS -> Audio`

On Wayland, game/window capture uses:

- `xdg-desktop-portal`;
- PipeWire;
- GStreamer.

Subtitle text is recognized using Tesseract OCR, translated locally from English to Polish and then synthesized using Piper TTS.

The current Polish voice is `pl_PL-gosia-medium`.

Narration runs locally after the required components have been downloaded. No cloud translation or online TTS service is required during normal use.

### Current limitations

The narrator is still experimental.

Known limitations include:

- some subtitles may be missed;
- short subtitles can be difficult to detect reliably;
- OCR can occasionally add incorrect characters or symbols;
- menus and other UI elements can sometimes be mistaken for subtitles;
- subtitle font and background affect recognition quality;
- translation is not always perfect;
- speech can be delayed depending on subtitle length and CPU performance;
- some games work considerably better than others;
- PipeWire/GStreamer compatibility can vary between desktop environments and systems.

The subtitle capture region can be configured per game.

More voices and additional languages are planned.

---

## Flatpak installation

Flatpak is the primary distribution format.

Download the latest `.flatpak` bundle from GitHub Releases and install it with:

```bash
flatpak install --user ./Game-Optimization-Linux-1.6.0-alpha-x86_64.flatpak
flatpak run io.github.DevVoidPL.GameOptimizationLinux
````

The Flatpak is designed to avoid depending on distribution-specific filesystem layouts.

Some features require interaction with host gaming tools or services. These integrations are handled separately, and missing optional host tools do not prevent the application from starting.

To use saved launch profiles from Steam, install the user-level runner shipped with the application:

```bash
flatpak run --command=game-optimization-install-runner \
  io.github.DevVoidPL.GameOptimizationLinux
```

No `sudo` is used.

The runner is installed to:

```text
~/.local/share/game-optimization-linux/bin/
```

---

## Per-game launch profiles

The Optimization page combines supported settings into a generated `LaunchPlan`.

Depending on the selected profile, this can include:

* GameMode;
* Gamescope;
* display and render/output resolution;
* FPS limits;
* MangoHud;
* Proton Tweaks;
* OptiScaler requirements;
* other supported per-game runtime changes.

Steam can use one permanent Launch Option:

```text
"~/.local/share/game-optimization-linux/bin/game-optimization-run" --appid APPID -- %command%
```

For example:

```text
"~/.local/share/game-optimization-linux/bin/game-optimization-run" --appid 480 -- %command%
```

Changing a Game Optimization Linux profile does not require replacing the Steam Launch Option each time.

The application generates the actual per-game launch plan when the game starts.

---

## MangoHud

Game Optimization Linux provides a graphical per-game MangoHud configuration.

MangoHud is also used by the experimental performance-measurement system.

Baseline recording uses its own temporary measurement configuration and does not need to overwrite the user's normal per-game MangoHud layout.

Recorded logs can be processed to provide data for:

* performance analysis;
* bottleneck detection;
* frame-limit detection;
* RAM/VRAM pressure analysis;
* future automatic optimization decisions.

Existing MangoHud logs can also be imported for analysis.

---

## OptiScaler and Proton

The OptiScaler integration reads stable release metadata from the official `optiscaler/OptiScaler` GitHub repository.

Downloads are cached and validated before extraction.

Installation is performed next to the selected game executable. When executable detection is ambiguous, the user can select the executable manually.

Game Optimization Linux records files managed by the OptiScaler integration so update, repair and removal operations do not intentionally delete unrelated mods.

Local `.7z` and `.zip` archives remain available as an advanced fallback.

Proton Tweaks are stored per Steam AppID and are disabled unless explicitly enabled by the user.

OptiScaler and DLL overrides can trigger anti-cheat systems or violate a game's online rules. Do not use them with anti-cheat protected or online games unless you have checked the game's policy and accept the risk.

Detection is a warning aid, not a guarantee of compatibility.

---

## Btrfs compression

Game compression currently works only on Btrfs.

ext4 and other filesystems do not support the same workflow.

Before recompression, Game Optimization Linux performs checks related to the installation path, filesystem, available space, running processes and Steam state.

Analysis itself is read-only.

Where supported, `compsize` can be used to obtain exact Btrfs compression information such as:

* physical disk usage;
* uncompressed extent size;
* compression ratio;
* current compression savings.

Exact host-side measurements can use the system Polkit authentication dialog.

Game Optimization Linux does not store or pass the administrator password itself.

Savings are not fixed.

Some games contain highly compressible assets, while others already use compressed archives and may save almost no additional space.

The application does not advertise a constant compression percentage as a guarantee.

Shared extents and reflinks require additional care because recompression or defragmentation can break sharing and potentially increase disk usage. Unsafe or uncertain operations should be blocked.

---

## Data and safety

Configuration follows the XDG base directory specification:

* profiles and settings: `$XDG_CONFIG_HOME/game-optimization-linux/`;
* caches: `$XDG_CACHE_HOME/game-optimization-linux/`;
* task and operation history: `$XDG_STATE_HOME/game-optimization-linux/`;
* managed application data and runner: `$XDG_DATA_HOME/game-optimization-linux/`.

The application avoids silently changing Steam configuration.

Steam Launch Options remain under the user's control.

Where possible, optimization changes are designed to be:

* per-game;
* previewable;
* measurable;
* reversible.

---

## Alpha status

Game Optimization Linux is still Alpha software.

Some features are much more mature than others.

The following areas should currently be considered experimental:

* Game Analyzer;
* performance baseline recording;
* bottleneck detection;
* Automatic Optimization;
* Polish Narrator;
* some Gamescope configurations;
* some Proton/OptiScaler combinations.

A feature working correctly with one game does not guarantee identical behavior with another game.

Real-world testing is extremely useful.

---

## Planned work

Current long-term plans include:

* larger Automatic Optimization library;
* deeper game-engine-specific optimization;
* improved CPU/GPU bottleneck analysis;
* improved RAM and VRAM optimization;
* better narrator OCR and lower narration latency;
* additional narrator voices;
* Spanish narrator support;
* Deep Optimize / unnecessary game-data cleanup;
* automatic per-game graphics enhancement / AI Remaster experiments;
* texture enhancement;
* broader launcher and library support;
* continued compatibility work across Linux distributions;
* additional Couch Mode improvements.

Planned features are not promises of a specific release date.

---

## Community

Feedback and real-game testing are welcome.

Game-specific reports are especially useful for:

* Game Analyzer;
* MangoHud baseline recording;
* Automatic Optimization;
* Gamescope;
* OptiScaler;
* Polish Narrator.

When reporting a problem, useful information includes:

* game name;
* Steam AppID;
* Linux distribution;
* desktop environment;
* Wayland or X11;
* CPU;
* GPU;
* Proton version;
* screenshots or logs.

Developer / Discord: `xvoiddeveloper`

GitHub Issues can also be used for reproducible bugs.

---

## Development

Python 3.12 or newer is required for a source checkout.

Create a virtual environment and install the project with development dependencies:

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

Run the native development build with:

```bash
.venv/bin/game-optimization-linux --desktop
```

Common checks:

```bash
.venv/bin/python -m pytest
.venv/bin/python -m compileall -q src tests
find src/game_optimization_linux/qml -name '*.qml' -exec qmllint {} +
```

Tests use temporary XDG directories and synthetic Steam/game trees.

Real game testing remains important because Steam, Proton, Gamescope, MangoHud, desktop portals and individual games can behave differently across Linux systems.

See [TESTING.md](https://github.com/DevVoidPL/Game-Optimization-Linux/blob/main/TESTING.md) for the test matrix and [docs/architecture.md](https://github.com/DevVoidPL/Game-Optimization-Linux/blob/main/docs/architecture.md) for the code layout.

---

## License

Game Optimization Linux is distributed under the MIT License.

Some optional components and third-party technologies used by individual features are distributed under their own licenses.


