<p align="center">
  <img src="src/game_optimization_linux/assets/branding/game-optimization-linux-app-icon.svg" width="96" alt="GameOpti">
</p>

<h1 align="center">GameOpti 2.3.0-beta</h1>

<p align="center">
  Beta version for Linux.<br>
  Manage your Linux games, per-game settings, graphics tools and performance measurements.<br>
  Couch Edition for a controller and TV, Desktop Edition for mouse and keyboard.
</p>

<p align="center">
  <a href="docs/release-notes-2.3.0-beta.md"><img src="https://img.shields.io/badge/2.3.0--beta-Pre--release-22c69a?style=for-the-badge" alt="2.3.0-beta pre-release"></a>
  <a href="https://github.com/DevLukaszNowak/Game-Optimization-Linux/releases/tag/v2.3.0-beta"><img src="https://img.shields.io/badge/Download-2.3.0_Beta-2563eb?style=for-the-badge&amp;logo=linux&amp;logoColor=white" alt="Download GameOpti 2.3.0 Beta"></a>
  <a href="https://www.youtube.com/@Void_Developer_PL"><img src="https://img.shields.io/badge/YouTube-Project_updates-ff0033?style=for-the-badge&amp;logo=youtube&amp;logoColor=white" alt="Project updates on YouTube"></a>
</p>

<p align="center">
  <a href="#features">Features</a> ·
  <a href="#couch-edition-and-desktop-edition">Editions</a> ·
  <a href="#interface">Interface</a> ·
  <a href="#download-and-install">Download</a> ·
  <a href="#documentation">Documentation</a>
</p>

<p align="center">
  <img src="docs/screenshots/beta-2-library-dark.png" width="960" alt="GameOpti dark library interface with sample games">
</p>

I started GameOpti to manage my own Linux games, launch settings and tools in
one place. It keeps settings per game and helps me check what a change does.

GameOpti 2.3.0 Beta fixes AppImage startup on Wayland, simplifies OptiScaler
management and restores missing Couch controls. It also improves Steam Launch
Options, Heroic/Lutris integration and game artwork. See the
[release notes](docs/release-notes-2.3.0-beta.md) for changes and limitations.

Compatibility depends on the game, launcher, hardware, drivers and installed
components. This beta has not been tested on every GPU or distribution. The
screenshots show earlier interface previews; some views use sample data.

Report problems through [GitHub Issues](https://github.com/DevVoidPL/Game-Optimization-Linux/issues).
Project updates, demonstrations and release news are on [YouTube](https://www.youtube.com/@Void_Developer_PL).

## Features

GameOpti handles per-game launching, performance analysis, Deep Compression
and resource management. It also manages external graphics and monitoring
tools, each with its own requirements and compatibility limits.

| | What it does |
| --- | --- |
| Game library and launchers | Finds games from Steam, Heroic and Lutris, including native and Flatpak installations. You can also add games manually. Includes library artwork, source filters and launcher backups. |
| Per-game profiles and launching | Keeps launch settings for each game instead of applying the same setup everywhere. GameOpti has its own Steam runner and can apply settings to Heroic and Lutris through their launch configuration. Profiles cover GameMode, display settings, resolution scaling, window modes and FPS limits. |
| Smart GPU Scope | Monitors resource pressure during a game session using Linux pressure and cgroup information. When CPU pressure stays high, it can adjust CPU weights for the isolated game and background processes you have opted in to. It tracks its own changes so they can be handled safely afterwards. This is not a custom GPU scheduler and it does not create extra VRAM. |
| GPU memory protection | On supported systems, GameOpti uses Linux `dmem.low` to give a game's GPU-memory allocations priority. This needs compatible kernel, driver and hardware support. It does not add physical VRAM or reduce a game's memory requirements. |
| Deep Compression | Analyses a game for files that may not be needed, such as unused language data, language-specific audio, old logs, caches, temporary files and installer leftovers. The analysis checks what can be handled safely; protected or uncertain files are not offered as ordinary cleanup targets. Selected files are moved to quarantine first, so you can test the game and restore them or remove the quarantine afterwards. Deep Compression does not require Btrfs. |
| Btrfs compression | Uses Btrfs zstd compression for game folders on Btrfs. GameOpti helps compare compression levels, measure storage before and after, track jobs and handle updates. This is separate from Deep Compression: it changes how files are stored, rather than deciding which game files can be removed. A separate deep-recompression operation is available for eligible Btrfs files. |
| Performance measurements | Lets you record a baseline, test a change and compare the results in the same game and scene. Measurements include FPS, 1% lows, frame times, power and memory where the required data is available. You can keep the change or go back to the previous setup. Results depend on the game and system; GameOpti does not promise an FPS increase for every title. |
| Gamescope and Aqueous | Supports Gamescope and Aqueous as optional compositor choices for runner launches. Their settings are kept separate from the rest of the per-game profile. |
| Upscaling and FSR | Manages OptiScaler installation, configuration, repair and restoration. Available FSR modes and variants include FSR 4 INT8 Lite and compatible community variants, subject to their own hardware and version requirements. |
| DLSS Enabler | Provides a separate per-game backend with configuration and diagnostics. Switching between DLSS Enabler and OptiScaler/Lite is explicit and reversible. |
| Frame generation | Select Off / lsfg-vk / MAKO Renderer / afmf-linux per game, with separate settings, on-demand component setup and compatibility checks. |
| Post-processing | Manages vkBasalt and ReShade presets, effects, effect order, parameters, shortcuts and per-game restoration. |
| MangoHud | Per-game configuration with a custom layout, colours, metric selection, placement, preview, FPS limiting and performance logging. |
| Proton tweaks | Per-game compatibility options applied through the launcher that starts the game. |
| Interface | Desktop Mode is available in both editions. Couch Edition also has a full-screen controller interface, controller settings and automatic switching to Couch Mode. Light and dark themes; English, Polish and Spanish translations. |

Check these requirements before using the tools:

- Btrfs compression and deep recompression need the game files to be on Btrfs. Deep Compression does not.
- GPU-memory protection requires compatible `dmem` support from the kernel and hardware/driver stack.
- Gamescope, Aqueous and the graphics backends need their own host or runtime dependencies. Availability can differ between native and Flatpak launchers.
- **lsfg-vk and MAKO require a separately purchased copy of Lossless Scaling** and compatible files. **afmf-linux is an independent project**, not AMD's official Linux driver feature.
- Graphics backends can conflict with each other or with individual games. GameOpti checks for known conflicts before applying settings, but it cannot guarantee compatibility with every title. Anti-cheat is not bypassed.

## Couch Edition and Desktop Edition

Both Flatpak editions use the Qt/QML frontend. Desktop provides the classic
mouse-and-keyboard interface. Couch includes Desktop and the full-screen
controller interface. The portable AppImage includes both modes.

The Flatpak editions share App ID `io.github.DevVoidPL.GameOptimizationLinux`
and user data. They replace each other within one installation; they are not
independent applications. Choose one edition and keep the same user or system
installation scope. Beta and stable branches also share data. Back up your
profiles, or use a separate Linux account to test without touching them.

| | Couch Edition | Desktop Edition |
| --- | --- | --- |
| Desktop Mode (mouse and keyboard) | ✓ | ✓ |
| Couch Mode for controller and TV | ✓ | — |
| Automatic switch to Couch Mode when a controller is used | ✓ | — |
| Controller settings | ✓ | — |
| Library, launchers, graphics, storage, MangoHud and measurements | ✓ | ✓ |

Couch Edition suits a living-room or handheld setup. Desktop Edition is for
mouse and keyboard use without Couch Mode.

## Interface

Each game page has sections for launch settings, Deep Compression, Btrfs tools,
graphics, MangoHud and performance measurements. Separate settings and status
indicators show which components are in use and what you can restore.

<table>
  <tr>
    <td width="50%" align="center">
      <img src="docs/screenshots/beta-2-gpu-memory.png" alt="GPU-memory protection and per-game optimization profiles">
      <br><strong>GPU memory & launch profiles</strong>
    </td>
    <td width="50%" align="center">
      <img src="docs/screenshots/beta-2-frame-generation.png" alt="Per-game frame-generation backend selection and setup flow">
      <br><strong>Frame generation</strong>
    </td>
  </tr>
  <tr>
    <td width="50%" align="center">
      <img src="docs/screenshots/beta-2-post-processing.png" alt="Post-processing backend, presets and per-game controls">
      <br><strong>Post-processing</strong>
    </td>
    <td width="50%" align="center">
      <img src="docs/screenshots/couch-home.png" alt="GameOpti Couch Mode with controller-oriented game selection">
      <br><strong>Couch Mode</strong>
    </td>
  </tr>
</table>

*Interface previews. Desktop images are cropped from project-owner captures; the
Couch preview uses demo data. [Screenshot notes](docs/screenshots/README.md).*

## Download and install

GameOpti is distributed through [GitHub Releases](https://github.com/DevLukaszNowak/Game-Optimization-Linux/releases/tag/v2.3.0-beta),
not Flathub. Flathub provides the runtime used by the Flatpak builds.

Download your chosen package and `SHA256SUMS` into the same directory, then
verify it before installing. See the [installation guide](docs/install-2.3.0-beta.md)
for updates, edition switching and checksum commands.

### Flatpak

Add the Flathub remote if it is not already configured:

```bash
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
```

Download one bundle from the release page and install the edition you chose:

```bash
# Couch Edition
flatpak install --user --reinstall ./GameOpti-2.3.0-beta-couch-x86_64.flatpak

# Desktop Edition
flatpak install --user --reinstall ./GameOpti-2.3.0-beta-desktop-x86_64.flatpak
```

Start GameOpti with:

```bash
flatpak run --user io.github.DevVoidPL.GameOptimizationLinux//beta --desktop
```

For an existing system-wide installation, use `--system` instead of `--user`
in the install and run commands. Do not create a second installation just to
switch editions.

For Steam, open a game's **Steam connection** panel. Apply requires
confirmation and may ask Steam to close before changing that game's Launch
Options. Existing arguments and known wrappers are preserved; ambiguous
commands are left for manual configuration. Saving a GameOpti profile alone
does not edit Steam Launch Options.

Heroic and Lutris use **Apply to launcher** and their existing launch
configuration. Tools must be available in the environment that runs the game,
including launcher Flatpaks.

To update, install the newer bundle over the existing one with the reinstall
option. To switch editions, install the other bundle; your application settings
are retained. Back them up before changing versions. Do not use `--delete-data`
when switching or removing only the beta. To remove the user Flatpak, use:

```bash
flatpak uninstall --user io.github.DevVoidPL.GameOptimizationLinux
```

### AppImage

Download the portable AppImage from the release page, make it executable and
run it:

```bash
chmod +x ./GameOpti-2.3.0-beta-x86_64.AppImage
./GameOpti-2.3.0-beta-x86_64.AppImage
```

The tested baseline is Ubuntu 22.04 userspace with **glibc 2.35**. Use glibc
2.35 or newer with compatible host graphics and C++ libraries; this is not a
guarantee for every distribution. Wayland and X11/XWayland startup were checked.
Keep the image at a stable path if Steam Launch Options point to it. Moving or
renaming it requires an explicit update of that command.

### Arch / AUR

The native Arch package is being prepared for AUR. It is not published yet;
the release source archive is available for building it.

## Future development

[GameOpti LXB](https://github.com/DevVoidPL/GameOpti-LXB) is a separate project
for controller and TV use, with LineXinBar integration planned. Work on the
main GameOpti application continues alongside it. See the [roadmap](ROADMAP.md)
for current work and future plans.

## Source layout

| Path | Contents |
| --- | --- |
| `src/game_optimization_linux/` | Application code, QML interface, translations and artwork. |
| `src/game_optimization_linux/qml/` | Qt/QML frontend used by both editions. |
| `data/` | Desktop entry, AppStream metadata and installation assets. |
| `flatpak/` | Build manifests and packaging tools for the Couch and Desktop editions. |
| `libexec/` | Helper programs installed with the application. |
| `scripts/` | Asset build scripts and the runner installer. |
| `packaging/appimage/` | AppImage builder and baseline instructions. |
| `docs/` | Feature guides and screenshots. |
| `.github/workflows/` | GitHub automation. |

## Documentation

- [Release status and plans](ROADMAP.md)
- [2.3.0 Beta release notes](docs/release-notes-2.3.0-beta.md)
- [Install and switch editions](docs/install-2.3.0-beta.md)
- [Architecture and launch flow](docs/architecture.md)
- [Previous release notes: 2.1](docs/release-notes-2.1.md)
- [Build the Flatpak](flatpak/README.md)

For source development, use Python 3.12 or newer:

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/game-optimization-linux --desktop
```

[Open an issue](https://github.com/DevVoidPL/Game-Optimization-Linux/issues)
with the application version, steps to reproduce the problem and **Copy system info**
from GameOpti. Remove personal data from any attached logs or screenshots.

## Credits and license

Created and maintained by **DevVoidPL**. Game Optimization Linux is licensed under
[MIT](LICENSE).

Some features are GameOpti's own code; others use or manage separate projects.
Thanks to the developers and contributors behind [GameMode](https://github.com/FeralInteractive/gamemode),
[Gamescope](https://github.com/ValveSoftware/gamescope), [Aqueous](https://github.com/Seafoam-Labs/Aqueous),
[MangoHud](https://github.com/flightlessmango/MangoHud),
[OptiScaler](https://github.com/optiscaler/OptiScaler),
[DLSS Enabler](https://github.com/artur-graniszewski/DLSS-Enabler),
[vkBasalt](https://github.com/DadSchoorse/vkBasalt), [ReShade](https://reshade.me/),
[lsfg-vk](https://lsfg-vk.dev/), [MAKO](https://github.com/eugeniosegala/MAKO),
[afmf-linux](https://github.com/serialexperimentslainnnn/afmf-linux),
[dmemcg-booster](https://gitlab.steamos.cloud/holo/dmemcg-booster), Wine/Proton,
DXVK, vkd3d-proton, Mesa and the Btrfs/compsize communities. The authors of the
FSR variants and shaders used by the integrations are credited as well.

Third-party tools, game artwork and trademarks remain the property of their
respective authors and retain their own licenses. Proprietary Lossless Scaling
files are not bundled with GameOpti.
