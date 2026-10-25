# Per-game frame generation

Open **Game details → Upscaling & effects → Frame generation** on Desktop, or
**Frame generation** in Couch game details. Use the D-pad to move or adjust,
A to confirm and B to close. Interpolation increases displayed frames; it does
not increase the game's simulation rate.

The setup flow is **Choose → Install → Resolve requirements → Configure → Apply**.
Installation, dependency readiness, saved settings and runtime evidence are
separate states. Advanced settings start collapsed. Reset changes the editor;
Apply saves the selection for the next launch.

## Backends

| Backend | Supported component | Additional requirement | Main controls |
| --- | --- | --- | --- |
| lsfg-vk | 2.0.0 | Separately purchased Lossless Scaling; `lsfg-vk.dll` from its `lsfg-vk` Steam branch | Multiplier, flow scale, performance model, FP16, swapchain compatibility; VSync pacing |
| MAKO Renderer | 4.6.0, standalone `render-v4.6.0` | Separately purchased Lossless Scaling; compatible public `Lossless.dll` | Multiplier, flow scale, model, FP16, base FPS cap, adaptive target and maximum multiplier |
| afmf-linux | 1.3.1 | No Lossless Scaling purchase | Fixed 2×, motion search/quality, pacing, minimum FPS, governor and HUD handling |

afmf-linux is an independent project, not AMD's official Linux driver feature.
Its supplied layer is 64-bit and needs Vulkan 1.1+ for interpolation. A Vulkan
1.0 application may only repeat frames despite a generated-frame counter.
lsfg-vk and MAKO supply 32/64-bit layers. GameOpti checks the selected executable's
architecture and the environment that actually runs it.

Downloads require confirmation and use pinned sizes and SHA-256 checks. Progress,
cancellation, retry, reinstall and removal are available. Components are shared;
settings remain per game and per backend. Installing a component does not enable
it globally. Existing installations owned by other tools are preserved.

For Lossless Scaling, use **Open Steam page**, automatic discovery or manual DLL
selection, then validate again. Detection and validation are separate. Steam
branches are never changed automatically, and proprietary DLLs are not bundled.

## Launching and compatibility

Steam uses GameOpti's runner. Heroic and Lutris use their existing managed launch
configuration. Launcher Flatpaks need access to the component and configuration
in their own environment. Runner setup and permission changes have restore actions.
A configured runner appears as ready, with repair as a secondary action.

Profiles live in `games/<key>/frame-generation.json`; lsfg-vk/MAKO use a dedicated
TOML configuration. **Off + Apply** blocks known managed generators for that game
on the next launch while keeping installed components and saved backend values.
The selected generator runs inside the game command, separately from Gamescope.
MAKO uses upstream `mako-launch` and the current configuration schema.

- Disable in-game or mod-provided frame generation before enabling an external
  generator. GameOpti does not silently stack them.
- afmf-linux is incompatible with OptiScaler injection in this integration. Use
  **Open OptiScaler** to reach its existing restore/remove flow, or select another
  backend. Navigation never removes files. Return and recheck requirements;
  unsaved frame-generation edits are retained.
- MAKO's managed path is SDR. Detected HDR and unsupported ReShade/layer
  combinations stop Apply instead of silently changing the graphics setup.
- Gamescope, HUD and post-processing combinations need testing in the chosen
  game. OptiScaler, DLSS Enabler and DXVK loaders are not overwritten.
- Anti-cheat warnings do not bypass protection. Use an appropriate offline or
  single-player test environment.

The Steam guard reads the selected game's current manifest. A complete
installation with a paused update is distinct from active file writes. Downloads,
staging, commit operations and incomplete/unknown states remain protected.
**Recheck requirements** is read-only and preserves pending edits.

## Runtime evidence and a practical test

A loaded module, saved configuration and confirmed generation are different
results. Runtime evidence must match the selected component, actual game
executable and session. For afmf-linux, initialization and nonzero generated
frames without interpolation errors are required. No real/output FPS split is
shown unless the measurement source can distinguish them.

1. Record the same scene with **Off**, unchanged graphics and a fresh launch.
2. Install one backend, resolve requirements and start with 2× and defaults.
   Use a stable base FPS cap; a Gamescope output limit is not a base FPS limit.
3. Apply, restart and compare the same scene. Check runtime diagnostics, camera
   motion, HUD edges, artifacts and input response; a higher overlay FPS alone
   is insufficient.
4. Test MAKO adaptive mode or afmf pacing/governor separately. Restore **Off**
   and restart to stop managed generation.

## Upstream references

- [lsfg-vk configuration](https://lsfg-vk.dev/docs/configuration/configuration-options/)
  and [environment variables](https://lsfg-vk.dev/docs/configuration/environment-variables/)
- [MAKO Renderer](https://github.com/eugeniosegala/MAKO/releases/tag/render-v4.6.0)
  and its [configuration reference](https://github.com/eugeniosegala/MAKO/blob/render-v4.6.0/engine/docs/CONFIGURATION-REFERENCE.md)
- [afmf-linux](https://github.com/serialexperimentslainnnn/afmf-linux/releases/tag/v1.3.1)
- [Khronos Vulkan layer interface](https://github.com/KhronosGroup/Vulkan-Loader/blob/main/docs/LoaderLayerInterface.md)
- [GOverlay](https://github.com/benjamimgois/goverlay), a workflow reference;
  GameOpti's interface and integration are implemented separately.
