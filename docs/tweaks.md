# Per-game Proton tweaks

Open **Optimization → Proton Tweaks**. In Couch Mode, choose **Optimization →
Proton Tweaks**. Choose an option, review **Preview changes**, then **Apply**.
Changes take effect on a fresh game launch. The editor keeps unsaved changes during
Refresh. Section and full resets change the draft; Apply saves them.

**Default** removes GameOpti's override and leaves the runner and launcher in
control. **Enabled** and **Disabled** explicitly override a supported boolean.
A workaround without a documented negative value offers Default and Enabled only.
Existing experimental options are retained for profile compatibility in Advanced;
they are not recommendations for new profiles.

| Category | Added controls | Requirements and purpose |
| --- | --- | --- |
| Display and input | Native Wayland | Selected Proton script and Wine Wayland driver; a Wayland session. Keep Default with Gamescope. Some overlays or launchers need XWayland. |
| Display and input | Disable HDR reporting | DXVK/DXGI with `DXVK_NO_HDR`; diagnose incorrect HDR reporting. Default keeps automatic detection. Configure actual HDR in the compositor, display and game. |
| Display and input | Prefer SDL controller input | Selected runner implementing `PROTON_PREFER_SDL`. Input compatibility; mappings and controller features may change. |
| Latency | Low Latency Layer | Selected runner must bundle the layer and binaries for the game architecture. The game must call Anti-Lag 2 or Reflex APIs. Enable the relevant in-game setting. |
| Latency | Expose Reflex through the layer | Low Latency Layer, compatible DXVK-NVAPI, and game support. Does not spoof GPU identity or force an unsupported game to offer Reflex. |
| Advanced compatibility | Disable ntsync | Selected runner implementing `PROTON_NO_NTSYNC`. Default keeps its synchronization choice. The kernel and accessible `/dev/ntsync` determine whether ntsync can run. |

The existing WineD3D, esync/fsync, NVAPI, GPU identity, legacy OpenGL and address-space
controls remain available. WineD3D is an OpenGL fallback for Direct3D 9–11, not a
replacement for vkd3d-proton's Direct3D 12 renderer. Some current runners already
enable large-address awareness or prefer ntsync; toggling these is not a general
performance optimization. Memory profiles, upscalers, frame generation, Gamescope
and FPS limits remain on their existing pages.

Proton logging now has an **Open log folder** action. The next launch writes
`steam-APPID.log` into the per-game GameOpti log folder in the launcher's own data
area. Logging can reduce performance; restore Default after diagnosis. Logs can
contain personal paths. No log folder is created by a status query.

## Launch integration

Steam uses the existing GameOpti runner. Overview shows its setup instructions and
connection status. Heroic and Lutris use **Overview → Apply to launcher** after
saving the profile. In Couch Mode, expand **Launch integration** in the tweaks
editor to apply the saved launcher plan or copy the existing Steam runner command.
Existing launcher backup, conflict and restore protections
also apply to tweaks. No Proton installation, driver or system configuration is
changed by this editor.

Runner support is detected from the selected runner's script and relevant files,
including layer binary architecture. An installed runner that is not selected does
not establish support. If Steam's automatic selection cannot be resolved, the UI
reports unconfirmed support. Host files alone do not prove a layer is loadable
inside Steam Linux Runtime or a launcher Flatpak. These environments still require
a fresh launch and runtime verification.

The preview shows GameOpti's changes and the resulting managed environment. User
launch arguments and unrelated variables are preserved. Existing multi-value
`DXIL_SPIRV_CONFIG` entries are merged rather than replaced. Default restores only
GameOpti-owned entries in launcher configurations.

Native Wine Wayland with managed Gamescope, Reflex with disabled NVAPI, and Low
Latency Layer with WineD3D or a selected external frame generator are flagged
before Apply. External layer disable switches are respected. Test one latency
path at a time. Saving settings and finding component files do not establish
reduced input latency or improved frame rates.

## Upstream references

- [Valve Proton runtime options](https://github.com/ValveSoftware/Proton#runtime-config-options)
- [GE-Proton options and HDR detection](https://github.com/GloriousEggroll/proton-ge-custom#options)
- [proton-cachyos options and retired variables](https://github.com/CachyOS/proton-cachyos#runtime-config-options)
- [DXVK configuration](https://github.com/doitsujin/dxvk/blob/master/dxvk.conf)
- [vkd3d-proton environment variables](https://github.com/HansKristian-Work/vkd3d-proton#environment-variables)
- [Mesa environment variables](https://docs.mesa3d.org/envvars.html)
- [Low Latency Layer requirements and modes](https://github.com/Korthos-Software/low_latency_layer#usage-and-configuration)
- [DXVK-NVAPI requirements](https://github.com/jp7677/dxvk-nvapi#requirements)

Current GE-Proton detects HDR automatically, while other releases expose different
HDR overrides. GameOpti therefore does not add a universal “force HDR” switch.
Mesa and vkd3d-proton already manage many defaults and workarounds internally;
experimental RT, descriptor-heap, shader-cache and memory overrides are not added
as generic speed switches.
