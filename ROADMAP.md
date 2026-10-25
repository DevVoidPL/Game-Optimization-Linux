# GameOpti Beta 2.0

Updated: **25 October 2026**.

GameOpti Beta 2.0 is being completed and polished, with bug fixing and testing
underway. The current public download is
[1.8.0 Alpha](https://github.com/DevVoidPL/Game-Optimization-Linux/releases/tag/v1.8.0-alpha).
Beta 2.0 has not been released; there is no fixed release date.

## Beta 2.0 scope

- **Library and launch integration:** Steam, Heroic, Lutris and manual games;
  per-game profiles, launcher configuration, previews, backup and restore.
- **Graphics:** OptiScaler, FSR modes and variants including INT8 Lite,
  DLSS Enabler, vkBasalt and ReShade.
- **Frame generation:** per-game selection of lsfg-vk, standalone MAKO Renderer
  or afmf-linux, with component management and backend-specific configuration.
- **GPU-memory protection:** supported host setup, per-game priority,
  capability-specific memory-pressure controls and effective-process diagnostics.
- **Monitoring and analysis:** MangoHud profiles and logging, gameplay recording,
  comparisons and explainable performance recommendations.
- **Storage:** Btrfs compression and measurement, update-aware jobs, deep
  analysis and reversible cleanup quarantine.
- **Narrator:** local subtitle capture/OCR, optional English-to-Polish
  translation and Polish speech, with per-game capture regions.
- **Interface:** Desktop and controller-oriented Couch Mode, light/dark themes
  and English, Polish and Spanish translations.
- **Additional tweaks and custom commands:** expanded compatibility and launch
  controls are part of the Beta scope; further implementation and polish remain.

This scope describes the development version, not the contents of the public
Alpha download. The [README](README.md) presents the interface and integrations.

## Work continuing before release

The focus is on completing the remaining configuration workflows, fixing bugs,
polishing layout and controller navigation, and testing real-game compatibility
across launchers, graphics stacks and Flatpak environments. Performance analysis,
Narrator accuracy and the interaction between graphics tools remain active areas
of refinement. Compatibility and performance depend on the game and system.

Expanded tweaks and custom commands are not marked complete. GPU-memory priority,
upscaling and frame generation are separate mechanisms; no universal FPS or
memory-saving result is promised.

AI Graphics Remaster is postponed and is not part of Beta 2.0.

## Follow development

[Void_Developer_PL on YouTube](https://www.youtube.com/@Void_Developer_PL) shares
progress and demonstrations. Public builds and release notes appear in
[GitHub Releases](https://github.com/DevVoidPL/Game-Optimization-Linux/releases).

[Report a problem](https://github.com/DevVoidPL/Game-Optimization-Linux/issues) ·
[Testing guide](TESTING.md) · [Support the project](README.md#support-gameopti)
