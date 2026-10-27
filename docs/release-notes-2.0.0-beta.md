# Game Optimization Linux 2.0.0 Beta

GameOpti Beta 2.0 manages your Linux game library, graphics, performance and storage
from one application. It is available as two alternative Flatpak bundles of the same
application; install one of them.

## Choose an edition

- **Couch Edition** – Desktop Mode plus full-screen Couch Mode for controller and TV,
  with automatic switching to Couch Mode when a controller is used.
- **Desktop Edition** – Desktop Mode only, for mouse and keyboard. No Couch Mode and no
  controller settings.

Both editions use the same App ID and the same settings. Installing the other bundle
switches the edition and keeps your profiles and data.

## Highlights

- Library and launchers: Steam, Heroic, Lutris and your own games.
- Launch profiles: Automatic, Maximum Performance, Balanced, Low Power, Lowest Latency,
  Stable Image, Best Quality and Custom, with GameMode and Gamescope.
- Measured optimization: record a baseline, test one change, compare measured results
  and keep or revert it. Results stay local to the game and system.
- GPU-memory protection on systems with the required dmem support.
- Upscaling with OptiScaler and FSR variants, DLSS Enabler, frame generation
  (lsfg-vk, MAKO, afmf-linux) and post-processing (vkBasalt, ReShade).
- MangoHud per-game configuration and performance logging.
- Btrfs compression with measurements and cleanup review.
- Narrator: local subtitle recognition, optional English-to-Polish translation and Polish
  speech with a built-in voice, plus your own Piper voices.

## Install

```bash
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install --user ./Game-Optimization-Linux-2.0.0-beta-couch-edition-x86_64.flatpak
# or
flatpak install --user ./Game-Optimization-Linux-2.0.0-beta-desktop-edition-x86_64.flatpak
```

Check the files against `SHA256SUMS`.

The built-in Voidek voice model is © DevVoidPL and has its own license
(`VOIDEK-LICENSE.md`, also inside the application under `/app/share/doc/game-optimization-linux/`);
it is not covered by the GameOpti MIT license.

Results depend on the game, hardware and configuration; GameOpti does not promise FPS
gains. Anti-cheat is never bypassed. lsfg-vk and MAKO need a separately purchased copy
of Lossless Scaling.

## Future development

GameOpti LXB is in development as a separate controller and TV application intended to
work with LineXinBar. AI Graphics Remaster, Smart GPU Scope and deeper game and engine
recognition are longer-term Beta 3.0 research directions, without a release date.
