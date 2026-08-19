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
