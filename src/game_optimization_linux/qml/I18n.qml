pragma Singleton
import QtQuick

QtObject {
    function text(value) {
        return String(value === undefined || value === null ? "" : value)
    }

    function profile(value) {
        switch (text(value)) {
        case "Fast": return qsTr("Fast")
        case "Balanced": return qsTr("Balanced")
        case "Maximum": return qsTr("Maximum")
        case "Auto": return qsTr("Auto")
        case "Maximum Performance": return qsTr("Maximum Performance")
        case "Quiet": return qsTr("Quiet")
        case "Custom": return qsTr("Custom")
        default: return status(text(value))
        }
    }

    function optimizationCategory(value) {
        switch (text(value)) {
        case "competitive": return qsTr("Competitive")
        case "fast_action": return qsTr("Fast action")
        case "cinematic": return qsTr("Cinematic single-player")
        case "platformer_2d": return qsTr("Platformer / 2D")
        case "strategy_simulation": return qsTr("Strategy / simulation")
        case "retro": return qsTr("Retro")
        case "custom": return qsTr("Custom")
        default: return qsTr("Unknown")
        }
    }

    function status(value) {
        var original = text(value)
        switch (original.trim().toLowerCase().replace(/_/g, " ")) {
        case "active": return qsTr("Active")
        case "analyzing": return qsTr("Analyzing")
        case "available": return qsTr("Available")
        case "calculating": return qsTr("Calculating…")
        case "calculating...": return qsTr("Calculating…")
        case "cancelled": return qsTr("Cancelled")
        case "checking": return qsTr("Checking")
        case "checking analysis cache": return qsTr("Checking analysis cache")
        case "compatible": return qsTr("Compatible")
        case "completed": return qsTr("Completed")
        case "completed with warning": return qsTr("Completed with warning")
        case "compressed": return qsTr("Compressed")
        case "blocked": return qsTr("Blocked")
        case "detected": return qsTr("Detected")
        case "error": return qsTr("Error")
        case "failed": return qsTr("Failed")
        case "drive disconnected": return qsTr("Drive disconnected")
        case "finalizing report": return qsTr("Finalizing report")
        case "fully supported": return qsTr("Fully supported")
        case "game-dependent": return qsTr("Game-dependent")
        case "healthy": return qsTr("Healthy")
        case "high": return qsTr("High")
        case "high benefit": return qsTr("High benefit")
        case "in progress": return qsTr("In progress")
        case "interrupted": return qsTr("Interrupted")
        case "loaded cached report": return qsTr("Loaded cached report")
        case "low": return qsTr("Low")
        case "low benefit": return qsTr("Low benefit")
        case "measuring existing compression": return qsTr("Measuring existing compression")
        case "measuring compression": return qsTr("Measuring compression")
        case "missing": return qsTr("Missing")
        case "missing files": return qsTr("Missing files")
        case "measurement failed": return qsTr("Measurement failed")
        case "moderate": return qsTr("Moderate")
        case "moderate benefit": return qsTr("Moderate benefit")
        case "needs attention": return qsTr("Needs attention")
        case "never": return qsTr("Never")
        case "not calculated": return qsTr("Not calculated")
        case "not checked": return qsTr("Not checked")
        case "not configured": return qsTr("Not configured")
        case "not detected": return qsTr("Not detected")
        case "not installed": return qsTr("Not installed")
        case "not requested": return qsTr("Not requested")
        case "not run": return qsTr("Not run")
        case "optional": return qsTr("Optional")
        case "partial support": return qsTr("Partial support")
        case "paused": return qsTr("Paused")
        case "planned": return qsTr("Planned")
        case "previously measured": return qsTr("Previously measured")
        case "queued": return qsTr("Queued")
        case "ready": return qsTr("Ready")
        case "restored": return qsTr("Restored")
        case "kept": return qsTr("Kept")
        case "reverted": return qsTr("Reverted")
        case "pending": return qsTr("Pending")
        case "not applied": return qsTr("Not applied")
        case "running": return qsTr("Running")
        case "scan incomplete": return qsTr("Scan incomplete")
        case "scanning files": return qsTr("Scanning files")
        case "supported": return qsTr("Supported")
        case "testing samples": return qsTr("Testing samples")
        case "uncompressed": return qsTr("Uncompressed")
        case "unsupported": return qsTr("Unsupported")
        case "unavailable": return qsTr("Unavailable")
        case "library unavailable": return qsTr("Library unavailable")
        case "unknown": return qsTr("Unknown")
        case "validating path": return qsTr("Validating path")
        case "verification required": return qsTr("Verification required")
        case "waiting for authorization": return qsTr("Waiting for authorization")
        case "verified": return qsTr("Verified")
        case "very high": return qsTr("Very high")
        default: return original
        }
    }

    function compressionClassification(value) {
        switch (text(value).trim().toLowerCase()) {
        case "strongly_compressed": return qsTr("Strongly compressed")
        case "moderately_compressed": return qsTr("Moderately compressed")
        case "low_effect": return qsTr("Low effect")
        case "no_compression": return qsTr("No compression")
        case "shared_extents_blocked":
            return qsTr("Operation blocked by shared extents or snapshots")
        case "measurement_unavailable": return qsTr("Measurement unavailable")
        default: return qsTr("Measurement unavailable")
        }
    }

    function updateStatus(value) {
        var original = text(value)
        switch (original) {
        case "Demo mode · update checks disabled":
            return qsTr("Demo mode · update checks disabled")
        case "Local-only mode · update checks disabled":
            return qsTr("Local-only mode · update checks disabled")
        case "Up to date":
            return qsTr("Up to date")
        default:
            return status(original)
        }
    }

    function scanMessage(value) {
        var original = text(value)
        var match
        switch (original) {
        case "Using safe demonstration data":
            return qsTr("Using safe demonstration data")
        case "Waiting to scan local Steam libraries":
            return qsTr("Waiting to scan local Steam libraries")
        case "Refreshing demonstration library":
            return qsTr("Refreshing demonstration library")
        case "Scanning local Steam libraries…":
            return qsTr("Scanning local Steam libraries…")
        case "Steam was not found in standard or configured locations":
            return qsTr("Steam was not found in standard or configured locations")
        case "Steam was found, but no installed games were detected":
            return qsTr("Steam was found, but no installed games were detected")
        default:
            break
        }

        match = original.match(/^Showing ([0-9]+) cached games while Steam is scanned$/)
        if (match)
            return qsTr("Showing %1 cached games while Steam is scanned").arg(match[1])
        match = original.match(/^Found ([0-9]+) games; calculating exact disk usage…$/)
        if (match)
            return qsTr("Found %1 games; calculating exact disk usage…").arg(match[1])
        match = original.match(/^Found ([0-9]+) games$/)
        if (match)
            return qsTr("Found %1 games").arg(match[1])
        match = original.match(/^Demo library ready · ([0-9]+) games$/)
        if (match)
            return qsTr("Demo library ready · %1 games").arg(match[1])
        match = original.match(/^Steam library ready · ([0-9]+) games · ([0-9]+) size scans unavailable$/)
        if (match)
            return qsTr("Steam library ready · %1 games · %2 size scans unavailable")
                    .arg(match[1]).arg(match[2])
        match = original.match(/^Steam library ready · ([0-9]+) games$/)
        if (match)
            return qsTr("Steam library ready · %1 games").arg(match[1])
        match = original.match(/^Steam library scan failed: (.+)$/)
        if (match)
            return qsTr("Steam library scan failed: %1").arg(message(match[1]))
        return original
    }

    function gameLabel(value) {
        var original = text(value)
        switch (original) {
        case "Steam library": return qsTr("Steam library")
        case "Unknown game": return qsTr("Unknown game")
        default: return original
        }
    }

    function taskLabel(value) {
        var original = text(value)
        var match
        switch (original) {
        case "Analysis": return qsTr("Analysis")
        case "Compression": return qsTr("Compression")
        case "Verification": return qsTr("Verification")
        case "Optimization": return qsTr("Optimization")
        case "Texture enhancement": return qsTr("Texture enhancement")
        case "Backup": return qsTr("Backup")
        case "Restore": return qsTr("Restore")
        case "Library scan": return qsTr("Library scan")
        case "Size calculation": return qsTr("Size calculation")
        case "Scan Steam libraries": return qsTr("Scan Steam libraries")
        case "Task": return qsTr("Task")
        default:
            break
        }
        match = original.match(/^Analyze (.+)$/)
        if (match)
            return qsTr("Analyze %1").arg(match[1])
        match = original.match(/^Calculate size: (.+)$/)
        if (match)
            return qsTr("Calculate size: %1").arg(match[1])
        match = original.match(/^Compress (.+) \((Fast|Balanced|Maximum|Auto)\)$/)
        if (match)
            return qsTr("Compress %1 (%2)").arg(match[1]).arg(profile(match[2]))
        match = original.match(/^Compress (.+)$/)
        if (match)
            return qsTr("Compress %1").arg(match[1])
        return original
    }

    function actionLabel(value) {
        var original = text(value)
        var match
        switch (original) {
        case "starting the library scan": return qsTr("starting the library scan")
        case "adding a manual demo game": return qsTr("adding a manual demo game")
        default:
            break
        }
        match = original.match(/^queuing analysis for (.+)$/)
        if (match)
            return qsTr("queuing analysis for %1").arg(match[1])
        match = original.match(/^queuing demo compression for (.+)$/)
        if (match)
            return qsTr("queuing demo compression for %1").arg(match[1])
        match = original.match(/^queuing compression for (.+)$/)
        if (match)
            return qsTr("queuing compression for %1").arg(match[1])
        match = original.match(/^saving setting (.+)$/)
        if (match)
            return qsTr("saving setting %1").arg(match[1])
        match = original.match(/^restoring demo backup (.+)$/)
        if (match)
            return qsTr("restoring demo backup %1").arg(match[1])
        match = original.match(/^deleting demo backup (.+)$/)
        if (match)
            return qsTr("deleting demo backup %1").arg(match[1])
        match = original.match(/^building launch preview for (.+)$/)
        if (match)
            return qsTr("building launch preview for %1").arg(match[1])
        match = original.match(/^loading optimization profile (.+)$/)
        if (match)
            return qsTr("loading optimization profile %1").arg(profile(match[1]))
        match = original.match(/^trying to (pause|resume|cancel) demo task (.+)$/)
        if (match) {
            var operation = match[1] === "pause" ? qsTr("pause")
                          : match[1] === "resume" ? qsTr("resume") : qsTr("cancel")
            return qsTr("trying to %1 demo task %2").arg(operation).arg(match[2])
        }
        return original
    }

    function message(value) {
        var original = text(value)
        var match
        switch (original) {
        case "Preliminary recommendation - game measurement required":
            return qsTr("Preliminary recommendation - game measurement required")
        case "Recommendation uses saved session measurements":
            return qsTr("Recommendation uses saved session measurements")
        case "No saved session measurements": return qsTr("No saved session measurements")
        case "Saved session measurements are available": return qsTr("Saved session measurements are available")
        case "A safe preliminary profile was used": return qsTr("A safe preliminary profile was used")
        case "Competitive profile follows the display refresh rate": return qsTr("Competitive profile follows the display refresh rate")
        case "Fast action favors responsive but bounded frame rate": return qsTr("Fast action favors responsive but bounded frame rate")
        case "A stable 60 FPS is a conservative starting point for this category": return qsTr("A stable 60 FPS is a conservative starting point for this category")
        case "Stability is preferred over maximum frame rate": return qsTr("Stability is preferred over maximum frame rate")
        case "Unknown category keeps a non-aggressive baseline": return qsTr("Unknown category keeps a non-aggressive baseline")
        case "User goal prioritizes low latency": return qsTr("User goal prioritizes low latency")
        case "User goal limits load and avoids an aggressive system profile": return qsTr("User goal limits load and avoids an aggressive system profile")
        case "User goal prioritizes image quality over maximum FPS": return qsTr("User goal prioritizes image quality over maximum FPS")
        case "gamemoderun or gamemoded is not installed": return qsTr("gamemoderun or gamemoded is not installed")
        case "GameMode is installed, but its service is unavailable": return qsTr("GameMode is installed, but its service is unavailable")
        case "GameMode is available (service diagnostic passed)": return qsTr("GameMode is available (service diagnostic passed)")
        case "gamescope is not installed": return qsTr("gamescope is not installed")
        case "Gamescope is available": return qsTr("Gamescope is available")
        case "gamescope --help failed": return qsTr("gamescope --help failed")
        case "Done": return qsTr("Done")
        case "MangoHud detected": return qsTr("MangoHud detected")
        case "MangoHud Flatpak layer detected":
            return qsTr("MangoHud Flatpak layer detected")
        case "MangoHud profile unavailable for this Steam installation":
            return qsTr("MangoHud profile unavailable for this Steam installation")
        case "MangoHud is not installed or its Vulkan layer is unavailable":
            return qsTr("MangoHud is not installed or its Vulkan layer is unavailable")
        case "MangoHud disabled for this game":
            return qsTr("MangoHud disabled for this game")
        case "MangoHud profile ready": return qsTr("MangoHud profile ready")
        case "MangoHud profiles require a Steam AppID":
            return qsTr("MangoHud profiles require a Steam AppID")
        case "Steam is already running without this Game Optimization MangoHud profile. Close Steam completely, then launch the game from Game Optimization.":
            return qsTr("Steam is already running without this Game Optimization MangoHud profile. Close Steam completely, then launch the game from Game Optimization.")
        case "Manual game entries are available only in Demo mode":
            return qsTr("Manual game entries are available only in Demo mode")
        case "The selected game could not be found":
            return qsTr("The selected game could not be found")
        case "Select a game before opening its details":
            return qsTr("Select a game before opening its details")
        case "Game analysis is not implemented yet":
            return qsTr("Game analysis is not implemented yet")
        case "Btrfs compression is not implemented yet":
            return qsTr("Btrfs compression is not implemented yet")
        case "Setting saved locally":
            return qsTr("Setting saved locally")
        case "Steam locations were saved and will apply after restart":
            return qsTr("Steam locations were saved and will apply after restart")
        case "Backup restore is not implemented yet":
            return qsTr("Backup restore is not implemented yet")
        case "Backup deletion is not implemented yet":
            return qsTr("Backup deletion is not implemented yet")
        case "Demo backup removed from the in-memory list":
            return qsTr("Demo backup removed from the in-memory list")
        case "Steam library scan failed; cached games remain available":
            return qsTr("Steam library scan failed; cached games remain available")
        case "Remove this library from Steam before forgetting its cache":
            return qsTr("Remove this library from Steam before forgetting its cache")
        case "The library path still exists and cannot be forgotten safely":
            return qsTr("The library path still exists and cannot be forgotten safely")
        case "A task for this library is still active":
            return qsTr("A task for this library is still active")
        case "Library cache was forgotten":
            return qsTr("Library cache was forgotten")
        case "Local settings could not be loaded; safe defaults are active":
            return qsTr("Local settings could not be loaded; safe defaults are active")
        case "System information is temporarily unavailable":
            return qsTr("System information is temporarily unavailable")
        case "The demo task queue could not be updated":
            return qsTr("The demo task queue could not be updated")
        case "The task list could not be updated":
            return qsTr("The task list could not be updated")
        case "Waiting for authorization to measure compression":
            return qsTr("Waiting for authorization to measure compression")
        case "unknown provider error":
            return qsTr("unknown provider error")
        case "directory changed or could not be read completely":
            return qsTr("directory changed or could not be read completely")
        case "directory could not be read":
            return qsTr("directory could not be read")
        case "Select an available game first":
            return qsTr("Select an available game first")
        case "The selected entry is not a Steam game":
            return qsTr("The selected entry is not a Steam game")
        case "Invalid Steam AppID":
            return qsTr("Invalid Steam AppID")
        case "Game installation directory not found":
            return qsTr("Game installation directory not found")
        case "Flatpak executable not found":
            return qsTr("Flatpak executable not found")
        case "Steam executable not found":
            return qsTr("Steam executable not found")
        case "Compression is available in the next implementation stage":
            return qsTr("Compression is available in the next implementation stage")
        case "A read-only analysis cannot be paused; it can be cancelled":
            return qsTr("A read-only analysis cannot be paused; it can be cancelled")
        case "A read-only analysis cannot be resumed":
            return qsTr("A read-only analysis cannot be resumed")
        case "Compression analysis was cancelled":
            return qsTr("Compression analysis was cancelled")
        case "The game path does not exist.":
            return qsTr("The game path does not exist.")
        case "A symbolic link cannot be used as the analysis root.":
            return qsTr("A symbolic link cannot be used as the analysis root.")
        case "The game path is not a directory.":
            return qsTr("The game path is not a directory.")
        case "The file scan reached its time limit; totals are partial.":
            return qsTr("The file scan reached its time limit; totals are partial.")
        case "The game appears to be running; future writes must wait.":
            return qsTr("The game appears to be running; future writes must wait.")
        case "Sampling was skipped because the analysis time limit expired.":
            return qsTr("Sampling was skipped because the analysis time limit expired.")
        case "The game directory is not writable by the current user.":
            return qsTr("The game directory is not writable by the current user.")
        case "The sample test stopped at its time limit.":
            return qsTr("The sample test stopped at its time limit.")
        case "ZSTD sampling support is unavailable; no numeric savings estimate was generated.":
            return qsTr("ZSTD sampling support is unavailable; no numeric savings estimate was generated.")
        case "compsize not installed":
            return qsTr("compsize not installed")
        case "compsize returned an unrecognized report":
            return qsTr("compsize returned an unrecognized report")
        case "Measured with compsize":
            return qsTr("Measured with compsize")
        case "Not run":
            return qsTr("Not run")
        case "Not run on a non-Btrfs filesystem":
            return qsTr("Not run on a non-Btrfs filesystem")
        case "Btrfs compression tools unavailable":
            return qsTr("Btrfs compression tools unavailable")
        case "The analysis report does not match this game path":
            return qsTr("The analysis report does not match this game path")
        case "The analyzed game path is unavailable":
            return qsTr("The analyzed game path is unavailable")
        case "A complete analysis is required":
            return qsTr("A complete analysis is required")
        case "The game is not on a verified Btrfs filesystem":
            return qsTr("The game is not on a verified Btrfs filesystem")
        case "The game directory is not writable":
            return qsTr("The game directory is not writable")
        case "The game is currently running":
            return qsTr("The game is currently running")
        case "Steam is currently installing or updating this game":
            return qsTr("Steam is currently installing or updating this game")
        case "Another write task is active for this game":
            return qsTr("Another write task is active for this game")
        case "Shared Btrfs extents were detected; recompression is blocked because defragmentation would break reflink sharing":
            return qsTr("Shared Btrfs extents were detected; recompression is blocked because defragmentation would break reflink sharing")
        case "Shared-extent risk could not be measured reliably; operation is blocked (fail closed)":
            return qsTr("Shared-extent risk could not be measured reliably; operation is blocked (fail closed)")
        case "The file plan could not be built completely":
            return qsTr("The file plan could not be built completely")
        case "Available Btrfs space could not be measured":
            return qsTr("Available Btrfs space could not be measured")
        case "Only verified Steam installations are supported":
            return qsTr("Only verified Steam installations are supported")
        case "The Steam library is unavailable":
            return qsTr("The Steam library is unavailable")
        case "The game model is not on Btrfs":
            return qsTr("The game model is not on Btrfs")
        case "Explicit confirmation is required":
            return qsTr("Explicit confirmation is required")
        case "The game path changed after planning":
            return qsTr("The game path changed after planning")
        case "The Steam manifest path does not match this library":
            return qsTr("The Steam manifest path does not match this library")
        case "Unknown compression plan":
            return qsTr("Unknown compression plan")
        case "Final verification failed":
            return qsTr("Final verification failed")
        case "Review and confirm the compression plan before starting":
            return qsTr("Review and confirm the compression plan before starting")
        case "This game update was ignored":
            return qsTr("This game update was ignored")
        case "The compression plan is no longer available":
            return qsTr("The compression plan is no longer available")
        case "The game library is unavailable":
            return qsTr("The game library is unavailable")
        case "Compression state requires verification after an interrupted operation":
            return qsTr("Compression state requires verification after an interrupted operation")
        case "Narrator settings saved":
            return qsTr("Narrator settings saved")
        case "Narrator settings could not be saved":
            return qsTr("Narrator settings could not be saved")
        case "This narrator component operation is already running":
            return qsTr("This narrator component operation is already running")
        case "Stop the narrator before changing its components":
            return qsTr("Stop the narrator before changing its components")
        case "Narrator component installed":
            return qsTr("Narrator component installed")
        case "Install the verified component through the application":
            return qsTr("Install the verified component through the application")
        case "The desktop portal session bus is unavailable":
            return qsTr("The desktop portal session bus is unavailable")
        case "The ScreenCast portal is unavailable":
            return qsTr("The ScreenCast portal is unavailable")
        case "The ScreenCast portal exposes no monitor or window sources":
            return qsTr("The ScreenCast portal exposes no monitor or window sources")
        case "Wayland portal capture is available":
            return qsTr("Wayland portal capture is available")
        case "GStreamer PipeWire capture is available":
            return qsTr("GStreamer PipeWire capture is available")
        case "The portal transport is provided by the application runtime":
            return qsTr("The portal transport is provided by the application runtime")
        case "The Tesseract OCR runtime is unavailable":
            return qsTr("The Tesseract OCR runtime is unavailable")
        case "Install the verified English OCR model":
            return qsTr("Install the verified English OCR model")
        case "Tesseract English subtitle OCR is ready":
            return qsTr("Tesseract English subtitle OCR is ready")
        case "Verified 3.9 MiB English tessdata_fast model":
            return qsTr("Verified 3.9 MiB English tessdata_fast model")
        case "Verified 64.2 MiB Argos OPUS model for local CPU translation":
            return qsTr("Verified 64.2 MiB Argos OPUS model for local CPU translation")
        case "Verified 60.3 MiB Polish Piper voice for local CPU speech":
            return qsTr("Verified 60.3 MiB Polish Piper voice for local CPU speech")
        case "PCM is sent directly to the sandbox audio service":
            return qsTr("PCM is sent directly to the sandbox audio service")
        case "Local English to Polish translation is ready":
            return qsTr("Local English to Polish translation is ready")
        case "The local CTranslate2 translation runtime is unavailable":
            return qsTr("The local CTranslate2 translation runtime is unavailable")
        case "Install the verified English to Polish translation model":
            return qsTr("Install the verified English to Polish translation model")
        case "Piper Polish speech synthesis is ready":
            return qsTr("Piper Polish speech synthesis is ready")
        case "The Piper CPU runtime is unavailable":
            return qsTr("The Piper CPU runtime is unavailable")
        case "Install the verified Polish Piper voice":
            return qsTr("Install the verified Polish Piper voice")
        case "The selected game is not running":
            return qsTr("The selected game is not running")
        case "Narrator is disabled for this game":
            return qsTr("Narrator is disabled for this game")
        case "No game subtitle adapter is available":
            return qsTr("No game subtitle adapter is available")
        case "The game exited":
            return qsTr("The game exited")
        case "No audio output device is available":
            return qsTr("No audio output device is available")
        case "The audio output does not support the narrator PCM format":
            return qsTr("The audio output does not support the narrator PCM format")
        case "Narrator PCM playback buffer could not be opened":
            return qsTr("Narrator PCM playback buffer could not be opened")
        case "Narrator audio playback failed":
            return qsTr("Narrator audio playback failed")
        case "Screen capture permission was cancelled":
            return qsTr("Screen capture permission was cancelled")
        case "Screen capture stopped":
            return qsTr("Screen capture stopped")
        default:
            break
        }
        match = original.match(/^Narrator components are unavailable: (.+)$/)
        if (match)
            return qsTr("Narrator components are unavailable: %1").arg(match[1])
        match = original.match(/^The selected (OCR|translation|speech) provider is not available: (.+)$/)
        if (match)
            return qsTr("The selected %1 provider is not available: %2").arg(match[1]).arg(match[2])
        match = original.match(/^The selected translation profile is not available: (.+)$/)
        if (match)
            return qsTr("The selected translation profile is not available: %1").arg(match[1])
        match = original.match(/^The selected Polish voice is not available: (.+)$/)
        if (match)
            return qsTr("The selected Polish voice is not available: %1").arg(match[1])
        match = original.match(/^OCR failed: (.+)$/)
        if (match)
            return qsTr("OCR failed: %1").arg(match[1])
        match = original.match(/^Translation failed: (.+)$/)
        if (match)
            return qsTr("Translation failed: %1").arg(match[1])
        match = original.match(/^Speech synthesis failed: (.+)$/)
        if (match)
            return qsTr("Speech synthesis failed: %1").arg(match[1])
        match = original.match(/^Audio playback failed: (.+)$/)
        if (match)
            return qsTr("Audio playback failed: %1").arg(match[1])
        match = original.match(/^Game category: (.+)$/)
        if (match)
            return qsTr("Game category: %1").arg(optimizationCategory(match[1]))
        match = original.match(/^Selected display: ([0-9]+)×([0-9]+) at ([0-9]+) Hz$/)
        if (match)
            return qsTr("Selected display: %1×%2 at %3 Hz").arg(match[1]).arg(match[2]).arg(match[3])

        match = original.match(/^Added (.+) to the demo library$/)
        if (match)
            return qsTr("Added %1 to the demo library").arg(match[1])
        match = original.match(/^Unknown page: (.+)$/)
        if (match)
            return qsTr("Unknown page: %1").arg(match[1])
        match = original.match(/^Analysis queued for (.+)$/)
        if (match)
            return qsTr("Analysis queued for %1").arg(match[1])
        match = original.match(/^Compression queued for (.+)$/)
        if (match)
            return qsTr("Compression queued for %1").arg(match[1])
        match = original.match(/^Automatic compression queued for (.+)$/)
        if (match)
            return qsTr("Automatic compression queued for %1").arg(match[1])
        match = original.match(/^Checking (.+) before automatic compression$/)
        if (match)
            return qsTr("Checking %1 before automatic compression").arg(match[1])
        match = original.match(/^Compression is unavailable for (.+) on (.+)$/)
        if (match)
            return qsTr("Compression is unavailable for %1 on %2").arg(match[1]).arg(match[2])
        match = original.match(/^(Fast|Balanced|Maximum|Auto) compression simulation queued for (.+)$/)
        if (match)
            return qsTr("%1 compression simulation queued for %2").arg(profile(match[1])).arg(match[2])
        match = original.match(/^Unknown setting: (.+)$/)
        if (match)
            return qsTr("Unknown setting: %1").arg(match[1])
        match = original.match(/^Demo backup for (.+) marked as restored$/)
        if (match)
            return qsTr("Demo backup for %1 marked as restored").arg(match[1])
        match = original.match(/^Demo launch requested for (.+)$/)
        if (match)
            return qsTr("Demo launch requested for %1").arg(match[1])
        match = original.match(/^Could not start Steam: (.+)$/)
        if (match)
            return qsTr("Could not start Steam: %1").arg(match[1])
        match = original.match(/^Starting (.+)$/)
        if (match)
            return qsTr("Starting %1").arg(match[1])
        match = original.match(/^Task (.+)$/)
        if (match)
            return qsTr("Task %1").arg(status(match[1]))
        match = original.match(/^(.+) completed$/)
        if (match)
            return qsTr("%1 completed").arg(taskLabel(match[1]))
        match = original.match(/^(.+) failed$/)
        if (match)
            return qsTr("%1 failed").arg(taskLabel(match[1]))
        match = original.match(/^Could not finish (.+)\. See the log for details\.$/)
        if (match)
            return qsTr("Could not finish %1. See the log for details.").arg(actionLabel(match[1]))
        match = original.match(/^The game path could not be inspected: (.+)$/)
        if (match)
            return qsTr("The game path could not be inspected: %1").arg(match[1])
        match = original.match(/^Write access could not be checked: (.+)$/)
        if (match)
            return qsTr("Write access could not be checked: %1").arg(match[1])
        match = original.match(/^Compression analysis is unavailable on (.+)\.$/)
        if (match)
            return qsTr("Compression analysis is unavailable on %1.").arg(match[1])
        match = original.match(/^Sample compression at level ([0-9]+) failed: (.+)$/)
        if (match)
            return qsTr("Sample compression at level %1 failed: %2").arg(match[1]).arg(match[2])
        match = original.match(/^Running-process detection failed: (.+)$/)
        if (match)
            return qsTr("Running-process detection failed: %1").arg(match[1])
        match = original.match(/^Filesystem detection failed: (.+)$/)
        if (match)
            return qsTr("Filesystem detection failed: %1").arg(match[1])
        match = original.match(/^Available space could not be measured: (.+)$/)
        if (match)
            return qsTr("Available space could not be measured: %1").arg(match[1])
        match = original.match(/^compsize could not be run: (.+)$/)
        if (match)
            return qsTr("compsize could not be run: %1").arg(match[1])
        match = original.match(/^compsize exited with status ([0-9]+)(.*)$/)
        if (match)
            return qsTr("compsize exited with status %1%2").arg(match[1]).arg(match[2])
        match = original.match(/^(.*); and ([0-9]+) more errors$/)
        if (match)
            return qsTr("%1; and %2 more errors").arg(match[1]).arg(match[2])
        match = original.match(/^Breaking shared extents could increase physical usage by up to ([0-9]+) bytes$/)
        if (match)
            return qsTr("Breaking shared extents could increase physical usage by up to %1 bytes")
                    .arg(match[1])
        match = original.match(/^Insufficient free space: ([0-9]+) bytes are required$/)
        if (match)
            return qsTr("Insufficient free space: %1 bytes are required").arg(match[1])
        match = original.match(/^Physical usage increased by ([0-9]+) bytes$/)
        if (match)
            return qsTr("Physical usage increased by %1 bytes").arg(match[1])
        match = original.match(/^Final verification failed: (.+)$/)
        if (match)
            return qsTr("Final verification failed: %1").arg(match[1])
        match = original.match(/^The Steam appmanifest could not be verified: (.+)$/)
        if (match)
            return qsTr("The Steam appmanifest could not be verified: %1").arg(match[1])
        return original
    }

    function analysisMessage(value) {
        var original = text(value)
        var match
        switch (original) {
        case "gpu_bottleneck": return qsTr("GPU bottleneck")
        case "cpu_bottleneck": return qsTr("CPU bottleneck")
        case "vram_pressure": return qsTr("VRAM pressure")
        case "ram_pressure": return qsTr("RAM pressure")
        case "frame_pacing_problem": return qsTr("Frame pacing problem")
        case "balanced": return qsTr("Balanced - no obvious bottleneck")
        case "insufficient_data": return qsTr("Insufficient data")
        case "Ray tracing": return qsTr("Ray tracing")
        case "Shadow quality": return qsTr("Shadow quality")
        case "Effects quality": return qsTr("Effects quality")
        case "Post-process quality": return qsTr("Post-process quality")
        case "View distance": return qsTr("View distance")
        case "Motion blur": return qsTr("Motion blur")
        case "Shadows": return qsTr("Shadows")
        case "Effects / volumetrics": return qsTr("Effects / volumetrics")
        case "Post-processing": return qsTr("Post-processing")
        case "Visual preference": return qsTr("Visual preference")
        case "Windows game using Steam compatibility layer":
            return qsTr("Windows game using Steam compatibility layer")
        case "Native Linux": return qsTr("Native Linux")
        case "filesystem signatures": return qsTr("filesystem signatures")
        case "no reliable signature": return qsTr("no reliable signature")
        case "manual override": return qsTr("manual override")
        case "executable not resolved": return qsTr("executable not resolved")
        case "resolved PE executable": return qsTr("resolved PE executable")
        case "resolved native executable": return qsTr("resolved native executable")
        case "active engine configuration": return qsTr("active engine configuration")
        case "Unity boot configuration": return qsTr("Unity boot configuration")
        case "no reliable active API setting": return qsTr("no reliable active API setting")
        case "executable header unavailable": return qsTr("executable header unavailable")
        case "FPS remained tightly clustered around one stable ceiling":
            return qsTr("FPS remained tightly clustered around one stable ceiling")
        case "The upper FPS distribution rarely exceeded that ceiling":
            return qsTr("The upper FPS distribution rarely exceeded that ceiling")
        case "Frametime remained clustered around the matching frame interval":
            return qsTr("Frametime remained clustered around the matching frame interval")
        case "GPU saturation can explain the observed ceiling without a frame limiter":
            return qsTr("GPU saturation can explain the observed ceiling without a frame limiter")
        case "GPU utilization is unavailable": return qsTr("GPU utilization is unavailable")
        case "Total CPU utilization cannot exclude a single-thread bottleneck":
            return qsTr("Total CPU utilization cannot exclude a single-thread bottleneck")
        case "CPU utilization is unavailable": return qsTr("CPU utilization is unavailable")
        case "The measured ceiling is close to the selected display refresh rate":
            return qsTr("The measured ceiling is close to the selected display refresh rate")
        case "FPS and frametime did not form a sufficiently tight stable ceiling":
            return qsTr("FPS and frametime did not form a sufficiently tight stable ceiling")
        case "FPS and frametime distribution data is incomplete":
            return qsTr("FPS and frametime distribution data is incomplete")
        case "The measured FPS also missed the configured target":
            return qsTr("The measured FPS also missed the configured target")
        case "Per-thread CPU utilization is unavailable, so CPU-bound confidence is limited":
            return qsTr("Per-thread CPU utilization is unavailable, so CPU-bound confidence is limited")
        case "CPU or GPU utilization is missing from the log":
            return qsTr("CPU or GPU utilization is missing from the log")
        case "VRAM pressure could not be calculated":
            return qsTr("VRAM pressure could not be calculated")
        case "The available metrics do not show one dominant saturated resource":
            return qsTr("The available metrics do not show one dominant saturated resource")
        case "Both sessions must contain representative comparable measurements":
            return qsTr("Both sessions must contain representative comparable measurements")
        case "GPU headroom improved while both representative sessions remained frame-limited":
            return qsTr("GPU headroom improved while both representative sessions remained frame-limited")
        case "Automatic recommends a conservative one-step test for the measured bottleneck.":
            return qsTr("Automatic recommends a conservative one-step test for the measured bottleneck.")
        case "No graphics reductions are recommended because the game appears frame-limited and the hardware has available headroom.":
            return qsTr("No graphics reductions are recommended because the game appears frame-limited and the hardware has available headroom.")
        case "No graphics reductions are recommended because the measured workload is balanced.":
            return qsTr("No graphics reductions are recommended because the measured workload is balanced.")
        case "The game installation path changed after analysis":
            return qsTr("The game installation path changed after analysis")
        case "The analyzed game path is no longer available":
            return qsTr("The analyzed game path is no longer available")
        case "The analyzed executable is no longer available":
            return qsTr("The analyzed executable is no longer available")
        case "Graphics settings changed after the saved baseline":
            return qsTr("Graphics settings changed after the saved baseline")
        case "Test the effect of this supported existing setting; the actual result must be measured":
            return qsTr("Test the effect of this supported existing setting; the actual result must be measured")
        case "Reduce the workload associated with this existing setting; the actual effect must be measured":
            return qsTr("Reduce the workload associated with this existing setting; the actual effect must be measured")
        case "Low; one existing text setting is changed and backed up":
            return qsTr("Low - one existing text setting is changed and backed up")
        case "Uses an existing game configuration value":
            return qsTr("Uses an existing game configuration value")
        case "Record a representative baseline before starting Automatic Optimization.":
            return qsTr("Record a representative baseline before starting Automatic Optimization.")
        case "No runtime optimization is necessary for the current measured target.":
            return qsTr("No runtime optimization is necessary for the current measured target.")
        case "The measured workload already meets the current target and has healthy frame pacing.":
            return qsTr("The measured workload already meets the current target and has healthy frame pacing.")
        case "Memory pressure was detected, but Automatic v1 has no safe runtime memory candidate.":
            return qsTr("Memory pressure was detected, but Automatic v1 has no safe runtime memory candidate.")
        case "A reversible runtime experiment is available. Its result must be measured before it can be kept.":
            return qsTr("A reversible runtime experiment is available. Its result must be measured before it can be kept.")
        case "No safe runtime optimization candidate is currently available.":
            return qsTr("No safe runtime optimization candidate is currently available.")
        case "Record representative performance data":
            return qsTr("Record representative performance data")
        case "Preserve the current healthy frame target":
            return qsTr("Preserve the current healthy frame target")
        case "Improve GPU-limited performance":
            return qsTr("Improve GPU-limited performance")
        case "Improve CPU-side consistency and performance":
            return qsTr("Improve CPU-side consistency and performance")
        case "Improve frame consistency": return qsTr("Improve frame consistency")
        case "Reduce measured VRAM pressure": return qsTr("Reduce measured VRAM pressure")
        case "Reduce measured RAM pressure": return qsTr("Reduce measured RAM pressure")
        case "Preserve the current balanced workload":
            return qsTr("Preserve the current balanced workload")
        case "GameMode": return qsTr("GameMode")
        case "Test whether GameMode improves CPU-side consistency or frame pacing":
            return qsTr("Test whether GameMode improves CPU-side consistency or frame pacing")
        case "Low - the per-game profile can be restored exactly":
            return qsTr("Low - the per-game profile can be restored exactly")
        case "None": return qsTr("None")
        case "The effective runner plan wrapped the measured game command with GameMode":
            return qsTr("The effective runner plan wrapped the measured game command with GameMode")
        case "The runner report belongs to a different measurement session":
            return qsTr("The runner report belongs to a different measurement session")
        case "The runner did not confirm comparison completion":
            return qsTr("The runner did not confirm comparison completion")
        case "GameMode was requested but was absent from the measured runner plan":
            return qsTr("GameMode was requested but was absent from the measured runner plan")
        case "Frame consistency improved even though average FPS was not the primary goal":
            return qsTr("Frame consistency improved even though average FPS was not the primary goal")
        case "The runtime candidate was not verified in the effective launch plan":
            return qsTr("The runtime candidate was not verified in the effective launch plan")
        case "Both measurements must be representative before the candidate is judged":
            return qsTr("Both measurements must be representative before the candidate is judged")
        case "Analyze the game and record a representative baseline first":
            return qsTr("Analyze the game and record a representative baseline first")
        case "Automatic Optimization requires a current representative baseline":
            return qsTr("Automatic Optimization requires a current representative baseline")
        case "Finish the current comparison cycle before starting Automatic Optimization":
            return qsTr("Finish the current comparison cycle before starting Automatic Optimization")
        case "The original representative baseline is unavailable":
            return qsTr("The original representative baseline is unavailable")
        case "The saved baseline changed after analysis; analyze the game again":
            return qsTr("The saved baseline changed after analysis; analyze the game again")
        case "An Automatic Optimization experiment is already active":
            return qsTr("An Automatic Optimization experiment is already active")
        case "Record and evaluate a representative comparison before keeping this optimization":
            return qsTr("Record and evaluate a representative comparison before keeping this optimization")
        case "The comparison is inconclusive; retry it or revert the optimization":
            return qsTr("The comparison is inconclusive; retry it or revert the optimization")
        }
        match = original.match(/^The stable regime continued for ([0-9.,]+) seconds$/)
        if (match) return qsTr("The stable regime continued for %1 seconds").arg(match[1])
        match = original.match(/^GPU load averaged ([0-9.,]+)%, leaving measured headroom$/)
        if (match) return qsTr("GPU load averaged %1%, leaving measured headroom").arg(match[1])
        match = original.match(/^Total CPU load averaged ([0-9.,]+)%$/)
        if (match) return qsTr("Total CPU load averaged %1%").arg(match[1])
        match = original.match(/^VRAM use reached ([0-9.,]+)% of detected capacity$/)
        if (match) return qsTr("VRAM use reached %1% of detected capacity").arg(match[1])
        match = original.match(/^RAM use reached ([0-9.,]+)% of detected capacity$/)
        if (match) return qsTr("RAM use reached %1% of detected capacity").arg(match[1])
        match = original.match(/^GPU load averaged ([0-9.,]+)% across the representative segment$/)
        if (match) return qsTr("GPU load averaged %1% across the representative segment").arg(match[1])
        match = original.match(/^CPU load averaged ([0-9.,]+)%$/)
        if (match) return qsTr("CPU load averaged %1%").arg(match[1])
        match = original.match(/^Total CPU load averaged ([0-9.,]+)% while GPU load was not saturated$/)
        if (match) return qsTr("Total CPU load averaged %1% while GPU load was not saturated").arg(match[1])
        match = original.match(/^(.+) changed by ([+-]?[0-9.,]+)%$/)
        if (match) return qsTr("%1 changed by %2%").arg(analysisMessage(match[1])).arg(match[2])
        match = original.match(/^(.+) is present in (.+)$/)
        if (match) return qsTr("%1 is present in %2").arg(match[1]).arg(match[2])
        match = original.match(/^Existing (.+) setting$/)
        if (match) return qsTr("Existing %1 setting").arg(analysisMessage(match[1]))
        return message(original)
    }

    function joinAnalysis(values, separator) {
        var translated = []
        var items = values || []
        for (var index = 0; index < items.length; ++index)
            translated.push(analysisMessage(items[index]))
        return translated.join(separator === undefined ? "; " : separator)
    }
}
