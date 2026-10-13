# GameOpti — development status and 2.0 Beta plans

Announcement updated: **13 October 2026**.

GameOpti is the short interface name of **Game Optimization Linux**.

## Available now: 1.8 Alpha

The current public release is
[Game Optimization Linux 1.8.0 Alpha](https://github.com/DevVoidPL/Game-Optimization-Linux/releases/tag/v1.8.0-alpha).
Use its release notes to distinguish shipped changes from development work.
Installation instructions are in [README.md](README.md#flatpak-installation).

The Alpha includes experimental features and known limitations. In particular,
Narrator accuracy and duplicate-dialogue prevention are still being improved.
Graphics Remaster functionality and the DLSS Enabler backend are not completed
features of 1.8 Alpha.

## In development and verification

- **Measured game optimization:** developing analysis-backed profiles and
  recommendations, with before/after measurements and verification of effects.
  The goal is real performance benefits, including potential FPS gains where
  supported by the game and configuration; no gain is guaranteed.
- **Polish Game Narrator / Lektor:** investigating OCR accuracy, subtitle-region
  handling, the quality of the first spoken line, and repeated dialogue.
  Passing unit tests alone does not establish correct behaviour in a game.
- **Compatibility and usability:** continued work on Desktop/Couch workflows,
  launcher integration, and Flatpak/host behaviour across Linux environments.

These areas are under development and testing. They are not a claim that all
changes are finished, validated, or present in the public download.

## Announced: GameOpti 2.0 Beta

**COMING IN ~1 MONTH** is an approximate planning estimate from this
announcement, not a fixed release date. **2.0 Beta is not available for download.**

The planned update focuses on measured optimization, better Polish narration,
and further improvements and new capabilities. Details will be revealed in
subsequent development updates; the final scope depends on implementation and
verification.

Local source builds may already carry an internal `2.0.0-beta` label or draft
release metadata. Neither is evidence of a public Beta release. Public
availability will be announced through
[GitHub Releases](https://github.com/DevVoidPL/Game-Optimization-Linux/releases).

## Planned and unfinished work

- More explainable per-game recommendations and wider performance validation.
- Extending measured FPS sessions beyond the current Steam runner workflow.
- Further Narrator accuracy and duplicate-prevention improvements.
- Further Couch Mode refinements and a refreshed public screenshot gallery.
- Graphics Remaster and DLSS Enabler integration, with scope and availability
  to be confirmed in future updates.
- Other new capabilities to be announced as development progresses.

This is a direction of work, not a guarantee that every item will ship in
2.0 Beta. It is not a changelog of completed changes.

## Follow the next updates

**[Subscribe to Void_Developer_PL on YouTube](https://www.youtube.com/@Void_Developer_PL)**
for progress reports, gameplay demonstrations, and upcoming reveals.

For contributor checks and the distinction between automated tests and
real-game verification, see [TESTING.md](TESTING.md).
