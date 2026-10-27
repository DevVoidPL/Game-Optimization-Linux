# MangoHud editor

Open a game's **MangoHud** page in Desktop or Couch Mode. Start with GameOpti green,
Minimal HUD or Diagnostic, then adjust Appearance, Metrics and Recording. The
position grid, offsets, colours and layout controls update an illustrative preview.
Its sample numbers are not measurements. **Real Vulkan preview** opens the host's
MangoHud with vkcube and the current draft, without saving the game profile.

**Apply** saves only this game's profile. Restart the game to load the new
configuration. Refresh preserves unsaved edits. **Reset this section** and
**Reset editor** change the draft; Apply commits the reset. Unknown profile fields
and user-added configuration options are retained. Global MangoHud configuration
is left alone. FPS limiting remains in **Optimization**.

In Couch Mode, use the D-pad to choose controls, left/right to adjust, and A to
activate. A on a numeric or text field opens the existing screen keyboard.
B closes the keyboard or colour picker before closing the editor.

## Monitoring support

The added controls target [MangoHud 0.8.4's documented configuration](https://github.com/flightlessmango/MangoHud/blob/v0.8.4/data/MangoHud.conf):
average FPS and its `0.01` FPS metric, detailed frametime, GPU/CPU/VRAM/RAM graphs,
GPU memory clock, throttling, process disk I/O, swap, API/version, Vulkan driver,
Wine synchronization, presentation mode and vkBasalt status.

GameOpti checks the launcher's environment and executable architecture. A host
installation does not prove availability inside a launcher Flatpak. New controls
remain unavailable when the version or configuration keys cannot be verified;
saved selections are kept. A 32-bit game needs its matching layer when MangoHud
runs inside the game.

Gamescope mangoapp monitors the compositor. Its readings do not establish the
game's API, driver, presentation, per-process memory/I/O or loaded vkBasalt layer.
Sensor availability also depends on the driver and permissions. Missing readings,
or an unsupported sensor displayed as zero by MangoHud, are not a measured zero.
A throttling indicator appears only while throttling is detected. FPS counters
alone do not separate generated frames or measure input latency.

Recording stays local and continues to use GameOpti's Performance Analysis flow.
Enable recording controls, choose the interval and duration, then use the logging
shortcut in the running game. This editor does not upload results.
