<p align="center"><strong>THE NEXT CHAPTER OF LINUX GAMING</strong></p>

<h1 align="center">GAMEOPTI 2.0 BETA<br>COMING IN ~1 MONTH</h1>

<p align="center">
  <a href="https://github.com/DevVoidPL/Game-Optimization-Linux/releases/tag/v1.8.0-alpha"><img src="https://img.shields.io/badge/Public_release-1.8_Alpha-2563eb?style=for-the-badge" alt="Current public release: 1.8 Alpha"></a>
  <img src="https://img.shields.io/badge/2.0_Beta-In_development-8b5cf6?style=for-the-badge" alt="2.0 Beta: in development">
  <a href="https://www.youtube.com/@Void_Developer_PL"><img src="https://img.shields.io/badge/YouTube-Follow_the_development-ff0033?style=for-the-badge&amp;logo=youtube&amp;logoColor=white" alt="Follow GameOpti development on YouTube"></a>
</p>

<p align="center">
  <strong>Measured optimization. Better Polish game narration. A bigger next step.</strong>
</p>

<p align="center">
  GameOpti is moving into its next stage of development.<br>
  We are developing real Linux game optimization built on measurements,
  analysis, and verification of the effects.<br>
  The goal is meaningful performance benefits, including potential FPS gains,
  depending on the game, hardware, and configuration.
</p>

<p align="center">
  We are also improving the Polish Game Narrator / Lektor to make dialogue
  listening more accurate, natural, and enjoyable during gameplay.<br>
  Further improvements and new capabilities are planned for 2.0 Beta;
  we will reveal more in upcoming development updates.
</p>

<p align="center">
  <strong>Follow <a href="https://www.youtube.com/@Void_Developer_PL">Void_Developer_PL on YouTube</a></strong><br>
  Subscribe for progress reports, gameplay demonstrations, and the next reveals.
</p>

<p align="center">
  <strong>Available now: 1.8 Alpha. 2.0 Beta is an announcement, not a download.</strong><br>
  About one month is our current planning estimate, not a fixed release date.
  Planned improvements are still in development; FPS gains are not guaranteed.<br>
  <a href="ROADMAP.md">Development status and plans</a>
  &nbsp;·&nbsp;
  <a href="https://github.com/DevVoidPL/Game-Optimization-Linux/releases/tag/v1.8.0-alpha">Download the public Alpha release</a>
</p>

---

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="src/game_optimization_linux/assets/branding/game-optimization-linux-horizontal-dark.svg">
    <img src="src/game_optimization_linux/assets/branding/game-optimization-linux-horizontal.svg" width="720" alt="Game Optimization Linux">
  </picture>
</p>

# Game Optimization Linux

Game Optimization Linux is an open-source desktop utility for organizing Linux
game libraries and managing per-game optimization, compatibility, graphics,
monitoring, storage, and accessibility tools from one interface.

> **Current public release: 1.8 Alpha.** 2.0 Beta is being developed and is not
> available for download. Back up important game files and review every proposed
> change, especially for modded or online games.

The technical overview below covers the development checkout, including work
that may differ from the published Alpha. For released changes, consult
[the 1.8.0 Alpha release notes](https://github.com/DevVoidPL/Game-Optimization-Linux/releases/tag/v1.8.0-alpha);
for unfinished work and release plans, see [ROADMAP.md](ROADMAP.md).

## Highlights

- One library for Steam, Heroic Games Launcher, Lutris, and Custom / Manual
  games, with launcher artwork and source filters.
- Editable, argument-safe launch configuration for manually added games.
- Per-game optimization profiles with GameMode, Gamescope, MangoHud, and
  Proton-related settings where supported.
- Managed OptiScaler installation and FSR-related configuration with backups,
  integrity checks, repair, and removal safeguards.
- Btrfs analysis, compression planning, measurement, verification, and task
  history with conservative safety checks.
- A growing game-analysis/automatic-optimization foundation. Graphics Remaster
  profiles and groundwork remain in development; the public 1.8 Alpha does not
  include completed Graphics Remaster functionality.
- Local Narrator / Lektor workflow for OCR, English-to-Polish translation, and
  offline speech components.
- Desktop Mode and a controller-oriented Couch Mode.

Optional tools remain optional: when GameMode, Gamescope, MangoHud, or another
component is unavailable, the application reports it instead of silently
pretending the integration is active.

## Supported game sources

| Source | Discovery | Basic launch |
| --- | --- | --- |
| Steam | Native and Flatpak libraries | Steam launch path and per-game runner profiles |
| Heroic | Native and Flatpak local metadata | Launcher-native invocation when available |
| Lutris | Native and Flatpak local metadata | Launcher-native invocation when available |
| Custom / Manual | User-managed entries | Native executable or explicitly configured Wine/runner command |

Launcher discovery is read-only. Games with the same title remain separate
when they belong to different launchers or installations.

## Screenshots

These images show earlier interface snapshots, not a finished 2.0 Beta release.
An updated, sanitized gallery is planned.

<details>
<summary>View the existing screenshot gallery</summary>

<table>
  <tr>
    <td width="50%" align="center">
      <img src="docs/screenshots/Zrzut%2009.png" alt="Unified games library in the light theme">
      <br><strong>Unified games library — Light</strong>
    </td>
    <td width="50%" align="center">
      <img src="docs/screenshots/Zrzut%20ekranu_20260825_161809.png" alt="Unified games library in the dark theme">
      <br><strong>Unified games library — Dark</strong>
    </td>
  </tr>
  <tr>
    <td width="50%" align="center">
      <img src="docs/screenshots/Zrzut%20ekranu_20260825_161925.png" alt="Per-game OptiScaler configuration">
      <br><strong>OptiScaler</strong>
    </td>
    <td width="50%" align="center">
      <img src="docs/screenshots/Zrzut%20ekranu_20260825_16809.png" alt="Local Narrator configuration">
      <br><strong>Narrator / Lektor</strong>
    </td>
  </tr>
</table>

</details>

## Flatpak installation

Flatpak is the primary distribution format. The current public download is
**1.8 Alpha**; **2.0 Beta is not available yet**. Download the `.flatpak` bundle
from [the 1.8.0 Alpha release](https://github.com/DevVoidPL/Game-Optimization-Linux/releases/tag/v1.8.0-alpha)
or browse [GitHub Releases](https://github.com/DevVoidPL/Game-Optimization-Linux/releases),
then run:

```bash
flatpak install --user ./Game-Optimization-Linux-*.flatpak
flatpak run io.github.DevVoidPL.GameOptimizationLinux
```

Steam optimization profiles use a small Python-free host runner installed for
the current user from the Flatpak:

```bash
flatpak run --command=game-optimization-install-runner \
  io.github.DevVoidPL.GameOptimizationLinux
```

No `sudo` is used. The runner is installed under
`~/.local/share/game-optimization-linux/bin/`.

### Linux and Flatpak notes

- Host tools and launcher data are detected independently; an unavailable
  integration does not prevent the rest of the application from starting.
- Paths outside the Flatpak's granted filesystem access may need explicit user
  permission and are reported as unavailable rather than scanned broadly.
- Btrfs compression features require a Btrfs game path. Exact extent reporting
  additionally depends on the supported host measurement component.
- OptiScaler/DLL injection can conflict with anti-cheat or game policies. The
  application does not provide anti-cheat bypasses.
- Steam launch options are not silently edited; the application presents the
  runner command for the user to review and copy.

## Current limitations

- Alpha releases and the upcoming Beta still require wider testing across
  distributions, desktop environments, GPUs, Wine/Proton versions, and game engines.
- Heroic and Lutris games get the optimization, MangoHud and OptiScaler settings
  through "Apply to launcher", which writes only the managed environment and
  wrapper entries into that game's own launcher configuration (with a backup and
  a restore button). It is refused while the launcher runs. Measured FPS
  sessions still need the Steam runner, and a Flatpak launcher needs GameMode,
  Gamescope and MangoHud inside its own sandbox.
- Manual (custom) games do not use the optimization pipeline.
- Automatic analysis and recommendations remain conservative and cannot prove
  compatibility or performance gains for every game.
- Optional integrations may be disabled when their host tools, permissions, or
  required game metadata are unavailable.

## Roadmap

The 2.0 Beta announcement, development priorities, and unfinished work are
tracked in [ROADMAP.md](ROADMAP.md). These are plans, not released features.

- Extend measured FPS sessions beyond Steam.
- Improve analysis-backed, explainable per-game recommendations.
- Continue compatibility, accessibility, and Flatpak integration testing.
- Publish a sanitized screenshot gallery from synthetic fixtures.

## Development and contributing

Python 3.12 or newer is required for a source checkout:

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/game-optimization-linux --desktop
```

A source checkout or a locally built Flatpak is a development build. Its
internal version label does not mean that 2.0 Beta has been publicly released.

Before submitting a change, run the checks relevant to the area you touched.
The complete test matrix and safety rules are in [TESTING.md](TESTING.md), and
the repository layout is documented in
[docs/architecture.md](docs/architecture.md). Bug reports should remove real
usernames, hostnames, paths, mount names, and game-library details from logs and
screenshots.

## License

Game Optimization Linux is distributed under the [MIT License](LICENSE).
