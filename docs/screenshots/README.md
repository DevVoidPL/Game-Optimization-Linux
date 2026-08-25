# Public screenshot checklist

This directory is reserved for sanitized, public screenshots of Game
Optimization Linux. Before adding an image, capture it with synthetic/demo data
and verify that it contains no username, hostname, real library, local path,
mount name, disk identifier, or personal configuration.

Expected files:

- `games-library.png` — unified library with fictional games;
- `game-overview.png` — overview of a synthetic game;
- `optimization.png` — optimization profile for a synthetic game;
- `graphics-remaster.png` — Graphics Remaster modes;
- `optiscaler.png` — OptiScaler configuration with synthetic paths;
- `narrator.png` — Narrator / Lektor configuration;
- `storage.png` — Btrfs/storage analysis using synthetic values.

The existing `tests/couch_tv_screenshot_probe.py` is safe for Couch Mode visual
inspection in its default mode. It does not produce this complete Desktop Mode
set, so its output should not be copied here as a substitute. Never pass
`--live` when preparing public screenshots.
