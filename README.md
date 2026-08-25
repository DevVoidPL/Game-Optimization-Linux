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

> **Alpha:** the project is under active development. Back up important game
> files and review every proposed change, especially for modded or online games.

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
- Graphics Remaster profiles and a growing game-analysis/automatic-
  optimization foundation.
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

## Flatpak installation

Flatpak is the primary distribution format. Download the current `.flatpak`
bundle from [GitHub Releases](https://github.com/DevVoidPL/Game-Optimization-Linux/releases),
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

- Alpha releases still require wider testing across distributions, desktop
  environments, GPUs, Wine/Proton versions, and game engines.
- Launcher-native Heroic/Lutris and manual launching do not yet use the full
  Steam optimization wrapper pipeline.
- Automatic analysis and recommendations remain conservative and cannot prove
  compatibility or performance gains for every game.
- Optional integrations may be disabled when their host tools, permissions, or
  required game metadata are unavailable.

## Roadmap

- Extend the optimization pipeline beyond Steam without bypassing launcher
  runtime configuration.
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

Before submitting a change, run the checks relevant to the area you touched.
The complete test matrix and safety rules are in [TESTING.md](TESTING.md), and
the repository layout is documented in
[docs/architecture.md](docs/architecture.md). Bug reports should remove real
usernames, hostnames, paths, mount names, and game-library details from logs and
screenshots.

## License

Game Optimization Linux is distributed under the [MIT License](LICENSE).
