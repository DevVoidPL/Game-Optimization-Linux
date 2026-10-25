# Per-game post-processing

Open **Game details → Upscaling & effects → Post-processing**. Couch Mode exposes
the same settings with D-pad navigation, A to confirm and B to close. File pickers
or an unusual executable path may still need a keyboard.

Choose one backend. Defaults are neutral, with no effects. Optional presets add
colour adjustments, antialiasing or light sharpening. Post-processing changes
the image; it is not an FPS optimization. Avoid combining multiple sharpening
passes. Depth-dependent AO/DoF is not offered without verified depth access.

## Supported paths

- **vkBasalt 0.3.2.10:** a Vulkan layer for native Vulkan or Windows games using
  DXVK/VKD3D. Native OpenGL and Wine wined3d do not use this path. GameOpti downloads
  the pinned Flathub 25.08 component on demand, checks 32/64-bit libraries and
  creates a dedicated per-game manifest and configuration.
- **ReShade 6.8.0:** the official standard Windows build from reshade.me, for
  supported D3D9/10/11/12 or OpenGL games through Wine/Proton. The proxy must match
  the executable architecture. Native Vulkan ReShade is not installed by this
  integration. Home opens ReShade's interface; Ctrl plus the chosen shortcut
  toggles effects.

A host installation is not proof that Steam Linux Runtime or a launcher Flatpak
can load it. The environment probe checks access and dependencies. Unknown API
detection is inconclusive rather than unsupported: select the actual API manually.
A real loader conflict is reported separately.

## Configuration and restoration

The editor provides enabled effects, ordering, expandable parameters, presets,
ReShade preset import and vkBasalt PNG/CUBE LUT import. Imported INI files accept
known effects and parameters; they do not execute add-ons or download arbitrary
files. Additional shaders outside the catalog require manual configuration.
Licenses and source information accompany the installed shaders.

**Apply** writes the configuration after the game is closed. Restart to load
changed effects, ordering or parameters. The toggle only switches effects already
loaded by the game. A neutral vkBasalt profile does not activate the layer, since
an empty upstream effect list would otherwise enable CAS.

- **Reset this effect** restores its parameters in the editor.
- **Restore default settings** clears enabled effects and restores parameter
  defaults and Home, preserving backend, API, imported files, backups and upscalers.
- **Off / restore** disables the integration and preserves presets in a
  `.gameopti-post-preserved-*` directory. Global vkBasalt settings and unrelated
  mods remain untouched.

OptiScaler, DLSS Enabler, DXVK and existing proxy mods can conflict with ReShade.
GameOpti does not overwrite their loaders or invent chaining arrangements. Use
vkBasalt where compatible, or explicitly restore/remove the conflicting component
through its own page first.

Steam requires the GameOpti runner. Apply the launcher profile after backend
changes for Heroic/Lutris. Effects are applied inside the game command when using
Gamescope. vkBasalt's shortcut needs X11/Xwayland; native Wayland keyboard toggling
is not guaranteed. Anti-cheat is not bypassed.

## Status and a practical test

Installed files, a loaded runtime and visibly working effects are separate
states. Diagnostics associate the game, session, configuration, module PID and
shader errors. Refreshing status neither reconfigures the backend nor overwrites
unsaved edits. Steam runner logs preserve a session stream because helper
processes may replace upstream log files.

1. **vkBasalt:** close the game, choose its Vulkan/DXVK/VKD3D path, enable Tonemap
   with Exposure=-0.5 and Apply. Launch offline into a stationary scene. Toggle
   with Home or the selected shortcut and compare brightness. Check the session
   log, then restore Neutral and test Off after a restart.
2. **ReShade:** use a game without a loader conflict. Select its API, install and
   enable a visible Vibrance adjustment. Launch offline, open Home and check
   the preset and shader messages. Compare with Ctrl+Home, then test Neutral,
   restart and Off while preserving custom presets.

Check image quality, text/HUD, HDR, stability and GPU cost in actual gameplay.
A Vulkan test application does not establish compatibility for every game.

Sources: [vkBasalt](https://github.com/DadSchoorse/vkBasalt),
[ReShade](https://github.com/crosire/reshade/tree/v6.8.0),
[official binaries](https://reshade.me/),
[FX headers](https://github.com/crosire/reshade-shaders/tree/fd0022170615ce0d8162d219bff07232fa6dd84f),
[SweetFX](https://github.com/CeeJayDK/SweetFX/tree/93ddf39b357f5da534ed6d34ba4ec8cc7dcfa361).
