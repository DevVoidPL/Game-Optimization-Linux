<p align="center">
  <img src="src/game_optimization_linux/assets/branding/game-optimization-linux-app-icon.svg" width="96" alt="GameOpti">
</p>

<h1 align="center">GameOpti Beta 2.0</h1>

<p align="center">
  Manage your Linux game library, graphics, performance and storage from one application.<br>
  A desktop interface for your mouse and keyboard. Couch Mode for your controller and TV.
</p>

<p align="center">
  <a href="ROADMAP.md"><img src="https://img.shields.io/badge/Beta_2.0-In_development-22c69a?style=for-the-badge" alt="Beta 2.0: in development"></a>
  <a href="https://github.com/DevVoidPL/Game-Optimization-Linux/releases/tag/v1.8.0-alpha"><img src="https://img.shields.io/badge/Download-1.8_Alpha-2563eb?style=for-the-badge&amp;logo=linux&amp;logoColor=white" alt="Download the public 1.8 Alpha"></a>
  <a href="https://www.youtube.com/@Void_Developer_PL"><img src="https://img.shields.io/badge/YouTube-Project_updates-ff0033?style=for-the-badge&amp;logo=youtube&amp;logoColor=white" alt="Project updates on YouTube"></a>
</p>

<p align="center">
  <a href="#inside-beta-20">Features</a> ·
  <a href="#interface">Interface</a> ·
  <a href="#download-and-install">Download</a> ·
  <a href="#documentation">Documentation</a> ·
  <a href="#support-gameopti">Support the project</a>
</p>

<p align="center">
  <img src="docs/screenshots/beta-2-library-dark.png" width="960" alt="GameOpti Beta 2.0 dark library interface with game covers">
</p>

**GameOpti Beta 2.0 is being completed and polished, with bug fixing and testing underway.**
The features below describe the Beta development scope; the current public download
remains **1.8 Alpha**. The Beta release will be announced on
[YouTube](https://www.youtube.com/@Void_Developer_PL) and
[GitHub Releases](https://github.com/DevVoidPL/Game-Optimization-Linux/releases).

## Inside Beta 2.0

GameOpti brings the tools around your games into one place: choose a game,
configure its profile, apply your changes and launch through its existing launcher.
Keep individual settings for each title rather than changing everything globally.

| | What you can manage |
| --- | --- |
| 🎮 **Library & launchers** | Steam, Heroic, Lutris and manually added games. Native and Flatpak launcher discovery, game artwork, source filters and launcher integration with backup and restore. |
| ⚙️ **Launch profiles** | Automatic, Maximum Performance, Balanced, Quiet and Custom profiles. GameMode, Gamescope, display settings, scaling, window modes and FPS limits. |
| 🧠 **GPU-memory protection** | Per-game GPU-memory priority on systems with the required dmem support, host-readiness checks and effective-process diagnostics. Memory-pressure controls follow the detected capabilities. |
| ✨ **Upscaling & FSR** | Managed OptiScaler installation, configuration, repair and restoration. FSR modes and variants include FSR 4 INT8 Lite and compatible community INT8 variants, with separate hardware and version requirements. |
| 🧩 **DLSS Enabler** | A separate per-game backend with configuration and diagnostics, plus explicit, reversible switching from OptiScaler/Lite. |
| 🎞️ **Frame generation** | Choose **Off / lsfg-vk / MAKO Renderer / afmf-linux** per game. On-demand component installation, backend-specific controls, separate saved settings and compatibility checks. |
| 🎨 **Post-processing** | **vkBasalt and ReShade** integration, presets, effect selection and ordering, parameter controls, toggle shortcuts and per-game restoration. Colour, contrast, sharpening and antialiasing in one panel. |
| 📊 **MangoHud** | Per-game presets, metric selection, colours, placement, layout preview, FPS limiting and performance logging. |
| 📈 **Performance analysis** | Record gameplay sessions, examine FPS, frame times and resource use, and compare profiles. Analysis-backed recommendations help you judge changes in the scene you actually play. |
| 💾 **Compression & cleanup** | Btrfs zstd compression, sample-based level selection, before/after storage measurements, update-aware compression and task history. Deep analysis and reversible cleanup quarantine help review reclaimable space. |
| 🎙️ **Narrator / Lektor** | Local subtitle recognition, optional English-to-Polish translation and Polish speech. Per-game subtitle regions, portal screen capture, voice selection and downloadable speech/translation components. |
| 🛠️ **Tweaks & custom commands** | Proton compatibility controls, with expanded tweaks and custom launch commands included in the Beta 2.0 scope. Further configuration and usability work is ongoing. |
| 🛋️ **Controller support** | Desktop and full-screen Couch Mode, controller navigation, clear focus, light/dark themes and English, Polish and Spanish interfaces. |

**A few requirements worth knowing:** Btrfs compression needs a Btrfs game folder.
GPU-memory protection depends on kernel, driver and host support; it prioritizes
allocations rather than adding physical VRAM. **lsfg-vk and MAKO require a separately
purchased copy of Lossless Scaling** and their respective compatible files.
**afmf-linux is an independent project**, not AMD's official Linux driver feature.
Graphics backends have different API, runtime and loader requirements, so GameOpti
checks conflicts before applying them. Anti-cheat is never bypassed.

## Interface

Focused per-game pages keep compression, optimization, upscaling, effects,
MangoHud and performance analysis within easy reach. Component installation,
saved settings and runtime status are presented separately. Backups and restore
actions stay alongside the integrations they belong to.

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

*Development interface previews. Desktop images are cropped from project-owner
captures; the Couch preview uses demo data. [Screenshot notes](docs/screenshots/README.md).*

## Download and install

The current public release is
[**Game Optimization Linux 1.8.0 Alpha**](https://github.com/DevVoidPL/Game-Optimization-Linux/releases/tag/v1.8.0-alpha).
Download its [x86_64 Flatpak bundle](https://github.com/DevVoidPL/Game-Optimization-Linux/releases/download/v1.8.0-alpha/Game-Optimization-Linux-1.8.0-alpha-x86_64.flatpak).
GameOpti is distributed through GitHub Releases, not Flathub; Flathub supplies its runtime.

```bash
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install --user ./Game-Optimization-Linux-1.8.0-alpha-x86_64.flatpak
flatpak run io.github.DevVoidPL.GameOptimizationLinux
```

For Steam profiles, install the user-level host runner:

```bash
flatpak run --command=game-optimization-install-runner \
  io.github.DevVoidPL.GameOptimizationLinux
```

GameOpti provides the runner setup instructions for Steam. Heroic and Lutris use
**Apply to launcher** and their existing launch configuration. Tools must be
available in the environment that runs the game, including launcher Flatpaks.

To update, download the newer public bundle and install it with
`flatpak install --user --reinstall ./Game-Optimization-Linux-*.flatpak`.
Application settings are retained. To remove the application, use
`flatpak uninstall --user io.github.DevVoidPL.GameOptimizationLinux`.

## Documentation

- [Project status and Beta 2.0 scope](ROADMAP.md)
- [Testing and contributing](TESTING.md)
- [Architecture and launch flow](docs/architecture.md)
- [Btrfs compression measurements](docs/compression_validation.md)
- [Narrator components and licenses](docs/narrator-components.md)
- [Build the Flatpak](flatpak/README.md)

For source development, use Python 3.12+:

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/game-optimization-linux --desktop
```

Found a problem? [Open an issue](https://github.com/DevVoidPL/Game-Optimization-Linux/issues)
with the application version, reproduction steps and **Copy system info** from
GameOpti. Remove personal data from any attached logs or screenshots.

## Support GameOpti

GameOpti is free and open source. Optional donations help fund development and testing.

<p align="center">
  <a href="https://buymeacoffee.com/voiddeveloperpl"><img src="https://img.shields.io/badge/Buy_Me_a_Coffee-Support_GameOpti-ffdd00?style=for-the-badge&amp;logo=buymeacoffee&amp;logoColor=black" alt="Support GameOpti on Buy Me a Coffee"></a>
  <a href="https://www.paypal.com/paypalme/GameOptimation"><img src="https://img.shields.io/badge/PayPal-Support_GameOpti-0070ba?style=for-the-badge&amp;logo=paypal&amp;logoColor=white" alt="Support GameOpti with PayPal"></a>
</p>

Follow **[Void_Developer_PL on YouTube](https://www.youtube.com/@Void_Developer_PL)**
for project updates, demonstrations and release news.

## Credits and license

Created and maintained by **DevVoidPL**. Game Optimization Linux is licensed under
[MIT](LICENSE). AI tools have assisted with development and documentation.

GameOpti builds on the work of [GameMode](https://github.com/FeralInteractive/gamemode),
[Gamescope](https://github.com/ValveSoftware/gamescope),
[MangoHud](https://github.com/flightlessmango/MangoHud),
[OptiScaler](https://github.com/optiscaler/OptiScaler),
[DLSS Enabler](https://github.com/artur-graniszewski/DLSS-Enabler),
[vkBasalt](https://github.com/DadSchoorse/vkBasalt), [ReShade](https://reshade.me/),
[lsfg-vk](https://lsfg-vk.dev/), [MAKO](https://github.com/eugeniosegala/MAKO),
[afmf-linux](https://github.com/serialexperimentslainnnn/afmf-linux),
[dmemcg-booster](https://gitlab.steamos.cloud/holo/dmemcg-booster) and their contributors.
Thanks also to the Wine/Proton, DXVK, vkd3d-proton, Mesa, Btrfs/compsize,
PipeWire, Tesseract, CTranslate2, OPUS-MT/Argos and Piper communities,
and the authors of the FSR variants and shaders used by the integrations.

Third-party tools, models, game artwork and trademarks remain the property of
their respective authors and retain their own licenses. Proprietary Lossless
Scaling files are not bundled with GameOpti.
