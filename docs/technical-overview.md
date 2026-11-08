# GameOpti 2.2.0 Technical Overview Information 

This document describes the implementation and integrations in the GameOpti 2.2.0 code line. It is intended for people who want to understand how the application works, what it changes, which upstream tools it uses, and where the relevant implementation lives.

GameOpti is not a universal FPS switch. It is a per-game Linux gaming management application with its own launch handling, profiling, measurement, Deep Compression, and resource-management code. It also manages selected third-party graphics, monitoring, and compatibility tools. Those projects remain separate components with their own licenses, requirements, and limitations.

Runtime availability is detected where possible. A feature being visible in the interface or having a saved profile does not by itself prove that the host, launcher sandbox, driver, or game can use it.

## 1. Technology stack

| Area | Technology | Role |
| --- | --- | --- |
| Main application language | Python 3.12+ | Application logic, controllers, services, providers, persistence, launch planning and host integration. |
| Qt interface | PySide6 / Qt 6 / Qt Quick (QML) | Application bootstrap, QML engine, controller facade and the Couch interface. |
| Data formats | JSON, TOML, YAML, CSV | Per-game profiles and journals, Aqueous rules, launcher configuration, MangoHud recordings and reports. |
| Archive handling | `py7zr` and a managed 7-Zip decoder where required by the package | Reading supported archives used by managed component installers. GameOpti validates archive members before installing files. |
| Linux process and resource interfaces | cgroup v2, systemd user manager, PSI, `/proc`, `/sys`, DRM client information | Game scopes, resource-pressure observations, process identity and GPU-memory diagnostics. |
| Storage | Btrfs, `btrfs-progs`, optional `compsize` | Filesystem compression, extent checks and physical-space measurements. |
| Packaging | Flatpak / Flatpak Builder | Sandboxed distribution and host-side runner hand-off. |
| Desktop build path | GTK 4, libadwaita, GNOME runtime and Nuitka | Separate compiled Desktop Edition packaging path documented in `flatpak/README.md`. |

The Python package declares PySide6, PyYAML and py7zr as runtime dependencies. `pytest` and `pytest-cov` are development dependencies. See [`pyproject.toml`](../pyproject.toml) for the package-level requirements.

## 2. Application architecture

The application is split into layers so the interface does not directly perform platform operations or edit game files.

```text
Desktop / Couch interface
          |
          v
     AppController                 QObject facade exposed to the UI
          |
          v
      Controllers                 Feature-specific UI/application workflows
          |
          v
       Services                   Planning, persistence, integrations and jobs
          |
          v
      Providers                   Linux, launcher and filesystem operations
          |
          v
 Linux / Steam / Proton / Btrfs / external tools
```

### Main layers

- `qml/` contains the Qt Quick interface, pages, shared components, themes and Couch-specific controls.
- `controllers/` contains feature controllers and `AppController`, the QObject facade exposed to QML. It owns UI-facing state and signals, then delegates work to domain services.
- `services/` contains the implementation of feature workflows: launch planning, profile repositories, compression, game analysis, cleanup, GPU-memory handling, MangoHud, graphics integrations and measured optimization.
- `providers/` contains platform-facing operations such as Steam discovery, Linux system inspection and guarded Btrfs commands.
- `models/` contains typed data structures passed between controllers, services and providers.
- `runner.py` is the Steam launch wrapper. It resolves saved settings and executes the game's argument vector.

Background work is kept out of the UI event path. Worker results are returned to the controller layer, which updates UI-facing snapshots and emits signals on the application thread. The current architecture is described in more detail in [`architecture.md`](architecture.md).

### Per-game state

GameOpti stores profiles by a stable game key rather than using one global set of settings for every title. The main XDG locations are:

| XDG location | Default directory | Typical contents |
| --- | --- | --- |
| Configuration | `~/.config/game-optimization-linux/` | Application settings, game profiles, launcher configuration state and manual-game definitions. |
| State | `~/.local/state/game-optimization-linux/` | Logs, task history, measurement sessions, MangoHud recordings and operation history. |
| Cache | `~/.cache/game-optimization-linux/` | Library and analysis caches. |
| Data | `~/.local/share/game-optimization-linux/` | Managed component data, deep-analysis plans and other persistent application data. |

The application honours absolute XDG overrides. Per-game profiles are stored below `config/games/<game-key>/`, with separate JSON files for features such as optimization, MangoHud, GPU memory, frame generation and post-processing. These files are not a replacement for the original launcher's own game metadata.

## 3. Game discovery and launch execution

### Game sources

The library layer discovers games from Steam, Heroic and Lutris, including supported native and Flatpak installations. It also supports manually configured games. Discovery and launch capability are separate checks: a game can appear in the library even when a particular integration cannot be applied through that launcher's sandbox.

Relevant implementation:

- `providers/steam.py`
- `providers/heroic.py`
- `providers/lutris.py`
- `controllers/library_scanner.py`
- `services/game_executable.py`

### Steam runner

Steam uses one stable GameOpti Launch Options command. The user-level runner is installed into the host home directory by the runner installer. On launch it:

1. Receives the Steam AppID and the original command after `--`.
2. Loads the saved per-game profiles.
3. Resolves the selected options into a launch plan.
4. Merges the applicable wrappers and environment variables.
5. Starts the game using an argument vector rather than evaluating a generated shell command.
6. Writes a launch report and manages any measurement or host-launch hand-off associated with the session.

The launch planner keeps the executable, arguments, wrappers and environment as separate data. It also reports the source and purpose of managed environment entries. Existing user environment values are generally preserved unless a narrowly scoped, documented conflict rule requires a change.

Relevant implementation:

- `runner.py`
- `services/runner_integration.py`
- `services/optimization_runtime.py`
- `services/optimization_profiles.py`

### Heroic and Lutris

Heroic and Lutris start games through their own launchers, so they do not use the Steam runner in the same way. GameOpti writes the managed environment and wrapper entries into the selected game's launcher configuration. It records the previous values and keeps backups so its own entries can be restored. It avoids writing launcher configuration while the corresponding launcher is running.

The launcher integration changes only the entries owned by GameOpti. Existing user arguments and unrelated configuration are retained. Availability is checked in the environment that actually launches the game; a host-installed library is not proof that a Flatpak launcher can load it.

Relevant implementation: `services/launcher_integration.py` and `services/launcher_native.py`.

### Manual games

Manual games have an explicit executable and launch configuration. They do not automatically inherit every Steam-specific integration. Their available features depend on the configured launch path and the host environment.

## 4. GameOpti's own resource-management systems

### 4.1 Smart GPU Scope

Smart GPU Scope is GameOpti's resource controller and a separate observer of GPU-memory protection state. It is not Gamescope, and it is not a custom GPU scheduler.

The resource controller samples Linux cgroup data for the isolated game scope, including:

- `cpu.pressure`, `memory.pressure` and `io.pressure`;
- CPU usage counters from `cpu.stat`;
- CPU throttling counters from `cpu.stat`;
- registered, explicitly opted-in GameOpti background scopes.

When an enabled mode detects sustained CPU contention and active game CPU demand, the controller can adjust **systemd `CPUWeight`** for the game scope and eligible background scopes. `CPUWeight` is a relative CPU scheduling weight; it is not a CPU quota or a hard CPU limit. The current policy uses different game/background weights for Balanced and Performance modes. It samples periodically and uses separate activation and restoration delays to avoid reacting to a single short spike.

The controller only works with dedicated GameOpti scopes. It does not migrate arbitrary existing processes into new cgroups or take ownership of shared desktop/launcher groups. The explicit background-launch path is restricted to supported applications such as Firefox, Chromium and Discord.

Changes are journaled with the original and applied values and a systemd scope identity. Before restoring a value, the controller checks whether the scope still has the same identity and whether the current value is still the one GameOpti applied. If another manager or the user changed it, GameOpti preserves the external change instead of overwriting it. Recovery is also arranged through a user systemd service so cleanup does not depend only on the GUI process remaining alive.

The GPU-memory observer is separate from those CPU policy writes. It follows the game scope and descendant processes, inspects DRM clients and the relevant ancestor `dmem.low` values, then reports state transitions such as `active_verified`, `active_unverified`, `externally_modified`, `waiting_for_gpu_process`, `degraded` or `unsupported`. A changed `dmem.low` value is evidence that the value changed; the observer does not claim to know which process or service changed it. The observer is read-only and does not repeatedly write GPU-memory limits.

Relevant implementation: `services/smart_gpu_scope.py` and `services/gpu_memory_host.py`.

### 4.2 GPU-memory protection

On systems exposing the required driver-backed `dmem` cgroup regions, GameOpti can place a game launch into a dedicated systemd/cgroup scope and apply best-effort `dmem.low` protection for the configured region.

Important boundaries:

- This uses Linux cgroup/dmem support; the kernel, GPU driver and device must expose a compatible region.
- It does not add physical VRAM, change the game's memory requirement or reserve a fixed VRAM partition.
- It does not set a hard `dmem.max` ceiling as part of the protection profile.
- Existing foreground-management services are detected rather than silently replaced by another scheduler.
- The game and normal telemetry run as the login user. The privileged host bridge is limited to preparing or restoring the system components required by the dmem integration.

The host integration uses Valve's `dmemcg-booster` to prepare the systemd hierarchy. When setup or restoration needs administrator authorization, the operation uses the host bridge and Polkit. Setup records the state it changed and restoration checks ownership before undoing it. GameOpti does not replace the kernel or change global package-manager configuration as part of this workflow.

The optional vkd3d-proton memory-pressure profile is a separate trade-off. It can add `no_upload_hvv` to `VKD3D_CONFIG` only when the actual selected Proton/vkd3d files pass the recorded hash checks, the game uses the supported D3D12 path, and the dedicated GPU's ReBAR capability has been verified. It is not a generic VRAM optimization and may trade GPU access performance for memory behaviour.

Relevant implementation: `services/gpu_memory.py`, `services/gpu_memory_host.py` and `docs/gpu-memory.md`.

## 5. Deep Compression and Btrfs storage operations

These are separate features with different purposes and requirements.

### 5.1 Deep Compression — game-content analysis and cleanup

Deep Compression analyses an installed game and identifies groups of files that may be unnecessary for the selected setup. It does not require Btrfs and does not mean Btrfs compression.

The analysis is implemented by `DeepOptimizationAnalyzer` and the classification rules in `deep_classify.py`. The scan is read-only: it creates a plan in GameOpti's data directory and does not modify the game directory. Classification uses evidence from paths, file names, directory context and selected game metadata. Language detection uses complete path segments or filename tokens rather than arbitrary substring matches. Wwise metadata can identify language-specific banks and shared sound-effect banks.

Depending on the game and the evidence available, analysis can group items such as:

- language-specific text and audio data;
- shared or neutral audio;
- old logs, crash dumps and temporary files;
- cache and leftover data;
- installer or redistributable files;
- backup copies and protected user data;
- files that are only candidates for Btrfs compression analysis.

The selected target language is kept. Required fallback language data is kept when the game has no separate target-language resource. Audio without a reliable language marker, shared audio banks, active Proton prefixes, possible save/configuration data, required game assets and other uncertain files are protected or left for review. The analyzer does not open or rewrite game archives to remove individual members.

Cleanup is a separate, explicit stage handled by `DeepCleanupExecutor`:

1. The user selects groups from the latest complete analysis plan.
2. The executor rejects stale or incomplete plans and validates the game root identity.
3. It checks that the game is not running and that a Steam update is not in progress.
4. Only files marked as removable by the plan are eligible. Symlinks, unsafe paths, changed roots and non-regular files are rejected.
5. Selected files are moved into a quarantine directory on the game's filesystem. The operation uses filesystem rename/link operations rather than copying files into an unrelated location.
6. A JSON transaction journal records the operation. The user can test the game, restore the quarantined files, or explicitly finalize the cleanup and free the space.

The quarantine is deliberately reversible until the user confirms finalization. A game test is part of the intended workflow: a candidate classification is not treated as proof that a game no longer needs a file.

Relevant implementation:

- `services/deep_optimization.py`
- `services/deep_classify.py`
- `services/deep_cleanup.py`
- `controllers/deep_optimization_controller.py`
- `controllers/deep_cleanup_controller.py`

### 5.2 Btrfs compression

The normal compression workflow uses Btrfs mechanisms. GameOpti manages the workflow around them: capability detection, planning, profile selection, execution, progress, before/after measurement, verification and task history. The compression algorithm itself is provided by Btrfs, not invented by GameOpti.

The provider checks the local `btrfs-progs` capabilities rather than assuming every installed version supports the same options. It can use Zstandard (`zstd`) compression settings for eligible game folders/files. It checks the game root and Steam manifest, monitors whether the game is running or updating, and checks for shared extents because rewriting shared data can increase physical allocation elsewhere.

Where available, `compsize` supplies physical and uncompressed size measurements. Btrfs extent information, including `btrfs filesystem du`, is used to identify shared-extent conditions. If exact measurement is unavailable, the application must report that limitation rather than present an estimate as an exact physical saving.

Relevant implementation:

- `providers/btrfs_compression.py`
- `services/compression.py`
- `services/btrfs_analysis.py`
- `services/compression_history.py`

### 5.3 Deep Recompression — separate Btrfs operation

Deep Recompression is a separate Btrfs-specific operation associated with the deep-analysis workflow. It is not Deep Compression.

Its planner is read-only. Execution is limited to individual eligible files and uses the syntax documented by the locally installed `btrfs-progs`. It does not run recursive defragmentation or `compress-force`. Shared/reflinked extents are excluded unless a separate, explicit risk path is provided by the operation. The feature is not reversible by GameOpti because Btrfs recompression rewrites file extents.

Sampling uses bounded file reads to estimate compressibility; the sample ratio is not a direct measurement of the Zstandard result. Exact physical-space results still depend on available filesystem measurement tools.

Relevant implementation: `services/btrfs_recompression.py`, `models/btrfs_recompression.py` and `controllers/deep_recompression_controller.py`.

## 6. Performance recording and measured optimization

GameOpti includes its own measurement-session lifecycle and comparison logic. MangoHud supplies the in-game telemetry log; GameOpti manages per-game recording configuration, starts and tracks sessions, parses the resulting data and evaluates comparisons.

### Recording pipeline

```text
Create baseline/comparison session
             |
             v
Generate per-game MangoHud configuration
             |
             v
Launch through Steam runner or supported launcher path
             |
             v
MangoHud writes periodic CSV samples
             |
             v
GameOpti validates and parses the session
             |
             v
Build metrics + run fingerprint + comparability result
             |
             v
Keep / Revert / Inconclusive recommendation
```

GameOpti's measurement configuration uses a 100 ms MangoHud logging interval. The measured-optimization pipeline excludes the initial 20-second warm-up and the final 5 seconds of the recording. A session needs at least 60 seconds of gameplay and 300 usable samples to meet the minimum measurement contract. These are sampling requirements, not a guarantee that two sessions are comparable.

Metrics include values when the relevant MangoHud or system source supplies them:

- average FPS and 1% low FPS;
- median, 95th-percentile and 99th-percentile frame time;
- stutters per minute;
- VRAM, GTT and RAM usage;
- GPU/CPU load, clocks, temperatures and power.

A stutter in the measured-session contract is a sample whose frame time is at least twice the session median and at least 8 ms above it. Unavailable sensors remain unavailable; missing telemetry is not treated as a measured zero. When frame generation is active, the session records whether the FPS value represents rendered or output frames so that generated output is not mislabelled as the game's simulation/render rate.

### Comparability and decision logic

Before judging a change, the comparison checks controlled fields such as game build, executable, display and render resolution, renderer/API, Proton runtime, in-game graphics settings, ray tracing, upscaler, frame generation, GPU and driver. The intended change is allowed to differ; other known differences reduce or invalidate comparability. Unknown fields cannot silently be treated as matching.

The comparison code uses named thresholds and returns the values, deltas, reasons and costs behind its result. Depending on the selected goal, the outcome can be **Keep**, **Revert** or **Inconclusive**. It does not force a positive result when samples are too short, settings differ, activation cannot be verified, or frame generation makes rendered-FPS interpretation invalid for that goal.

The local game fingerprint is evidence-based. It can inspect executable imports, selected Proton runtime information and saved GameOpti profiles. Unknown API/engine information remains `Unknown`; it is not guessed from a game's title or genre.

Relevant implementation:

- `services/performance_session.py`
- `services/performance_analysis.py`
- `services/measured_optimization/metrics.py`
- `services/measured_optimization/fingerprint.py`
- `services/measured_optimization/comparison.py`
- `services/automatic_optimization.py`

## 7. External graphics, monitoring and compatibility tools

GameOpti manages profiles, configuration and launch integration for the tools below. The upstream projects provide the underlying graphics layers, algorithms, overlays or compositor behaviour.

| Tool / project | How GameOpti uses it | Important boundary |
| --- | --- | --- |
| **GameMode** | Adds GameMode to a per-game launch plan when it is available in the selected launch environment. | Availability inside Steam Linux Runtime or a launcher Flatpak is checked separately from host installation. |
| **Gamescope** | Adds an optional compositor wrapper and manages per-game display, scaling, window and FPS-limit settings. | Gamescope is an external compositor. It is not Smart GPU Scope. GameOpti scopes identified game-only environment entries around the wrapper and preserves unrelated host settings. |
| **Aqueous** | Optional compositor choice for supported runner launches; GameOpti manages per-game TOML rules, nested launch and status observations. | The current integration does not expose per-app VRR/HDR. Its window observation does not invent a window-to-PID relationship. |
| **MangoHud** | Generates and applies a per-game HUD configuration, layout, colours, metric selection, position and recording settings. | GameOpti targets the documented MangoHud 0.8.4 configuration keys and checks availability in the actual launcher environment. The global MangoHud configuration is left alone. |
| **OptiScaler** | Per-game installation, configuration, update, repair, verification and restoration. Also provides the managed basis for supported FSR variants. | OptiScaler's loader files can conflict with other injection backends. GameOpti checks conflicts and keeps a manifest of managed files. |
| **FSR 4 variants** | Manages compatible FSR 4 driver/model or community upscaler variants through the OptiScaler integration. | Hardware, version and variant combinations are distinct. The driver component and community upscaler variant are not interchangeable or always composable. Downloads use pinned artifact metadata and hashes. |
| **DLSS Enabler** | Separate per-game backend with configuration and diagnostics. GameOpti provides an explicit, journaled switch between DLSS Enabler and the managed OptiScaler installation. | The backends are mutually exclusive where they claim the same proxy DLLs. The switch parks/restores the recorded files rather than stacking both loaders. |
| **lsfg-vk** | Optional per-game frame-generation backend; GameOpti manages its component setup and configuration. | Requires a separately purchased Lossless Scaling copy and compatible `lsfg-vk` files. |
| **MAKO Renderer** | Optional per-game frame-generation backend with its own configuration and adaptive controls. | Requires separately purchased Lossless Scaling files. GameOpti validates the actual game architecture and launcher environment. |
| **afmf-linux** | Independent frame-generation backend with its own mode, pacing, governor and HUD controls. | It is an independent Linux project, not AMD's official Linux driver feature. The documented integration is incompatible with OptiScaler injection. |
| **vkBasalt** | Vulkan post-processing layer with per-game presets, effects, ordering, parameters and shortcuts. | Requires a Vulkan path (including DXVK/VKD3D where applicable); it is not an FPS optimisation switch. |
| **ReShade** | Standard Windows ReShade integration for supported D3D/OpenGL games through Wine/Proton, with per-game presets and restoration. | The managed integration does not install native Vulkan ReShade. Proxy architecture and existing loader conflicts matter. |
| **Proton / DXVK / vkd3d-proton** | GameOpti inspects the selected runner and manages per-game compatibility environment options. | It does not replace Proton or force an option unsupported by the selected runner. A detected host component does not prove that the actual game runtime can load it. |

### Frame-generation versions documented by the current integration

The frame-generation guide documents these component targets:

| Backend | Documented component target | External requirement |
| --- | --- | --- |
| lsfg-vk | 2.0.0 | Lossless Scaling purchase and compatible `lsfg-vk.dll` from its Steam branch. |
| MAKO Renderer | 4.6.0 (`render-v4.6.0`) | Lossless Scaling purchase and compatible `Lossless.dll`. |
| afmf-linux | 1.3.1 | Independent project; no Lossless Scaling purchase. |

Frame generation interpolates or otherwise generates output frames. It does not increase the game's simulation rate. GameOpti treats saved configuration, installed files, a loaded runtime module and verified generated-frame output as different states.

More detailed guides:

- [`frame-generation.md`](frame-generation.md)
- [`gpu-memory.md`](gpu-memory.md)
- [`mangohud.md`](mangohud.md)
- [`post-processing.md`](post-processing.md)
- [`tweaks.md`](tweaks.md)

## 8. Aqueous and compositor-specific details

Aqueous profiles are saved per application matcher. The integration writes marked sections into `aqueous/rules.toml` and validates the TOML before replacing the file. It creates separate rules for Wayland `app_id` and XWayland `class` matching. Supported settings include layout, fullscreen, output, workspace, anchor, size and buffer-scale policy. Per-app VRR and HDR are explicitly rejected by the current settings validator.

When Aqueous is selected, GameOpti starts the game command in the compositor's child environment. This keeps the compositor itself outside the resource-controlled game cgroup. The integration can read `aqueousctl windows --json` for window observations, but it does not guess a PID from a window ID.

Gamescope settings remain a separate part of the launch profile. When a game runs through Gamescope, GameOpti isolates only identified game-specific loader/preload entries and restores the relevant environment for the actual Steam command. Unknown user-supplied environment values are preserved where possible; unresolved conflicts are surfaced rather than silently rewritten.

Relevant implementation: `services/aqueous.py` and `services/optimization_runtime.py`.

## 9. Installation, host bridge and security boundaries

### Flatpak host integration

The Flatpak application and the game runner do not share the same filesystem view. GameOpti therefore installs a small user-level host runner under:

`~/.local/share/game-optimization-linux/bin/`

The host bootstrap copies the packaged runner shim atomically and checks the file type and SHA-256 before deciding whether it needs to be refreshed. Host-side commands that must cross the Flatpak boundary use `flatpak-spawn --host` and predefined operations. The host bridge is not a general-purpose shell interface.

### Privileged operations

The application refuses to start as root. Ordinary UI work, game launches and resource telemetry run as the login user. Polkit is used only for documented host operations that require elevated privileges, such as the dmem host preparation/restoration path. The host bridge validates operation arguments and records ownership evidence so it can avoid undoing changes made by another service or by the user.

### File and launch safety

Several integrations use the same conservative principles:

- Build commands as argument vectors rather than concatenated shell strings.
- Validate game IDs, paths, executable architecture and selected launcher context.
- Avoid following symlinks when operating on game or launcher files.
- Keep manifests or journals for installed files and configuration changes.
- Preserve unrelated user settings and restore only entries owned by GameOpti.
- Check whether a game is running or updating before operations that modify its installation.
- Pin component versions, sizes and SHA-256 hashes for managed downloads where the integration defines pinned artifacts.

GameOpti does not bypass anti-cheat. Compatibility warnings are not a mechanism for disabling anti-cheat protection.

## 10. Known limits and interpretation of results

- An installed component is not proof that it loaded in a specific game session.
- A host library may not be visible inside Steam Linux Runtime or a launcher Flatpak.
- GPU-memory protection depends on a supported kernel, driver and exposed `dmem` region. It does not add VRAM or change a game's memory footprint.
- CPU weights are relative scheduling weights, not CPU limits. Smart GPU Scope does not implement memory reclaim, IO-priority control or a GPU scheduler.
- Deep Compression is a file-analysis and quarantine workflow. It is not Btrfs compression, and it does not require a Btrfs filesystem.
- Btrfs compression and Deep Recompression require Btrfs. Deep Recompression rewrites file extents and is not reversible by GameOpti.
- MangoHud logs are periodic samples, not every rendered frame. FPS and frame-time metrics should be interpreted using the recorded sample interval and session validity.
- Frame-generation output FPS is not the same as the game's simulation/render FPS. Generated frames do not mean the game processes more simulation steps per second.
- Performance results depend on the game, scene, hardware, driver, compositor, launcher and other active components. GameOpti does not promise an FPS increase for every title.

## 11. Source map

| Area | Main source files |
| --- | --- |
| Application bootstrap | `app.py`, `main.py`, `config.py` |
| UI facade and controllers | `controllers/app_controller.py`, `controllers/` |
| Launch planning and Steam runner | `services/optimization_runtime.py`, `services/runner_integration.py`, `runner.py` |
| Heroic/Lutris integration | `services/launcher_integration.py`, `services/launcher_native.py` |
| Smart GPU Scope | `services/smart_gpu_scope.py` |
| GPU memory and host bridge | `services/gpu_memory.py`, `services/gpu_memory_host.py` |
| Deep Compression | `services/deep_optimization.py`, `services/deep_classify.py`, `services/deep_cleanup.py` |
| Btrfs compression | `providers/btrfs_compression.py`, `services/compression.py`, `services/btrfs_analysis.py` |
| Deep Recompression | `services/btrfs_recompression.py`, `models/btrfs_recompression.py` |
| Performance analysis | `services/performance_session.py`, `services/performance_analysis.py`, `services/measured_optimization/` |
| MangoHud | `services/mangohud.py`, `models/mangohud.py` |
| Upscaling and DLSS | `services/optiscaler.py`, `services/fsr_variants.py`, `services/dlss_enabler.py` |
| Frame generation | `services/frame_generation.py`, `services/frame_generation_artifacts.py`, `services/frame_generation_isolation.py` |
| Post-processing | `services/post_processing.py`, `services/post_processing_artifacts.py`, `services/post_processing_catalog.py` |
| Aqueous | `services/aqueous.py` |
| Flatpak build and release packaging | `flatpak/README.md`, `flatpak/` |
