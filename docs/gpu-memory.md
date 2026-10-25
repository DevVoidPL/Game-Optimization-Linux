# GPU-memory management

Open **Game details → Optimization → GPU memory** on Desktop or Couch. The main
switch protects the next game session where supported. Advanced controls expose
a separate memory-pressure profile, host setup/restore and recording comparisons.
Reset changes the editor; Apply saves it. Graphics settings and installed backends
are preserved.

## Protection and eviction are different mechanisms

- **dmem cgroups** account for and protect driver-exposed memory regions.
  GameOpti uses best-effort `dmem.low`, without imposing a hard minimum or
  changing `dmem.max`. See the [cgroup v2 documentation](https://docs.kernel.org/admin-guide/cgroup-v2.html).
- **TTM/AMDGPU eviction changes** improve buffer selection and overcommit
  handling. They are separate from controller availability. The probe records
  the running kernel, regions and relevant TTM BTF structures/functions;
  a kernel version alone does not prove the complete patch set is present.
  See [upstream TTM](https://github.com/torvalds/linux/blob/v7.3/drivers/gpu/drm/ttm/ttm_bo.c)
  and the [CachyOS backport](https://github.com/CachyOS/kernel-patches/blob/master/7.0/misc/0001-cgroup-vram.patch).

[Valve's dmemcg-booster](https://gitlab.steamos.cloud/holo/dmemcg-booster) prepares
the systemd hierarchy. [KCGroups/foreground integration](https://github.com/pixelcluster/kcgroups)
and [Gamescope](https://github.com/ValveSoftware/gamescope) can independently
adjust foreground protection. GameOpti detects existing services rather than
creating a competing scheduler. Without foreground integration, a protected
GameOpti session remains protected in the background too.

## Host setup and launch support

Requirements include cgroup v2, a driver-exposed dmem region, a systemd user
manager, host Python 3 and dmemcg-booster. On CachyOS, the GUI can prepare the host
through Polkit using the signed distribution package. It does not replace the
kernel, refresh package databases or change global pacman configuration. Other
distributions need their supported dmemcg-booster/kernel setup first.

Host restoration preserves installed packages, independently enabled services
and values changed later by the user. Disabling one game's profile does not
stop system services. Setup state is stored under
`/var/lib/game-optimization-linux/gpu-memory-setup.json`.

The GUI and game remain unprivileged. A user scope is created before GPU
allocation so Proton/launcher descendants inherit it; systemd cleans it up after
exit. Runtime checks match the actual executable, DRM client, memory region and
ancestor protection. A wrapper PID or running booster alone is insufficient.

Native Steam uses the existing runner. Native Heroic/Lutris use managed launcher
wrappers; configured manual games use their launch path. Unsupported launcher
Flatpak-to-host scope paths are reported explicitly. Buffers already allocated
by a running game are not moved into another cgroup.

The panel separates system readiness, saved profile, effective process protection
and measured benefit. **GameOpti Off does not imply no protection:** Gamescope or
desktop integration may still protect the game. `dmem.current` covers the entire
cgroup, including other processes sharing it.

GPU vendor names do not activate support. The driver must expose a real region.
Shared-memory devices do not receive a dedicated-VRAM ReBAR profile.

## Optional memory-pressure profile

The default preserves allocator settings. The optional documented vkd3d-proton
[`no_upload_hvv`](https://github.com/HansKristian-Work/vkd3d-proton/blob/master/README.md)
setting limits CPU-visible VRAM used by UPLOAD heaps. It can free VRAM at the cost
of GPU access performance; it is a conditional trade-off, not a universal 8 GB fix.

This profile requires the supported Steam D3D12 path, a dedicated GPU with a
confirmed large BAR and compatible 32/64-bit Proton DLLs. Their versions and hashes
are saved; changing Proton requires reapplying the profile. Existing flags are
merged. DXVK's default memory management is left unchanged.

GameOpti does not treat reported-VRAM overrides as savings, enlarge GTT, change
swap/sysctls or clear caches. Resolution, textures and ray tracing are preserved.

## Compare recordings

Use the existing performance-recording flow for the same scene and loading time.

1. Save Off with the default allocator. Record a representative 60–90 seconds,
   then close the game normally.
2. Enable protection, Apply and use a fresh launch for the same scene and graphics.
   Confirm effective protection of the actual process. Test the UPLOAD option
   in a separate pair.
3. Compare recordings and repeat, preferably reversing the order. If independent
   protection is active in both runs, the comparison cannot attribute a difference
   to the GameOpti switch. Independent services are not disabled to manufacture
   a contrast.

Measurements include available device VRAM/GTT, DRM residency/shared memory,
cgroup use, RAM pressure, process RSS, GPU errors and existing MangoHud FPS/p95/p99.
Unavailable budgets remain unavailable. Shared RSS and cgroup totals can include
other processes. At least ten matching process/DRM observations are needed.

The comparison's improvement criterion requires FPS +5%, p99 −5%, p95 without
more than 5% regression, valid recordings and no observed GPU errors. Its stutter
count uses recorded samples above max(50 ms, twice the median), not every frame.
One pair does not prove a repeatable benefit. Higher residency can be useful;
8 GB remains 8 GB, and protection alone cannot repair broken materials or a game's
texture-streaming logic.
