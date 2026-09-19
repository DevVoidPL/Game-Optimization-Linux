pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"
import ".." as App

FocusScope {
    id: page
    objectName: "couchSettings"
    property var controller
    property var navigation
    property real couchScale: 1.0
    property int selectedIndex: 0
    property int activeCategoryIndex: 0
    property var settingsData: controller && controller.settings ? controller.settings : ({})
    property var narratorGlobalData: ({})
    property string editingSettingId: ""
    property int ignoredLibraryIndex: 0
    property int pendingMenuVolume: -1
    property int pendingMusicVolume: -1
    readonly property int effectiveMenuVolume: pendingMenuVolume >= 0
            ? pendingMenuVolume : Number(setting("couchMenuSoundsVolume", 40))
    readonly property int effectiveMusicVolume: pendingMusicVolume >= 0
            ? pendingMusicVolume : Number(setting("couchMusicVolume", 20))
    readonly property bool keyboardOpen: onScreenKeyboard.opened
    readonly property var narratorComponents: controller && controller.narratorComponents
            ? controller.narratorComponents : []

    readonly property var categories: [
        { "id": "general", "title": qsTr("General"), "subtitle": qsTr("Language, appearance and startup") },
        { "id": "libraries", "title": qsTr("Libraries"), "subtitle": qsTr("Game locations and storage") },
        { "id": "automation", "title": qsTr("Automation"), "subtitle": qsTr("Updates and compression") },
        { "id": "controller", "title": qsTr("Controller"), "subtitle": qsTr("Input and Couch behaviour") },
        { "id": "audio", "title": qsTr("Audio"), "subtitle": qsTr("Menu sounds and music") },
        { "id": "narrator", "title": qsTr("Narrator"), "subtitle": qsTr("Local speech components") },
        { "id": "advanced", "title": qsTr("Advanced"), "subtitle": qsTr("Resources, diagnostics and mode") }
    ]
    readonly property var rows: [
        { "id": "language", "category": "general", "title": qsTr("Language"), "value": languageLabel(setting("language", "English")), "enabled": true, "description": qsTr("Choose the interface language.") },
        { "id": "appearance", "category": "general", "title": qsTr("Appearance"), "value": themeLabel(setting("themeMode", "system")), "enabled": true, "description": qsTr("Follow the system colours or force a light or dark interface.") },
        { "id": "automatic-updates", "category": "automation", "title": qsTr("Automatic update checks"), "value": boolLabel(setting("automaticUpdates", true)), "enabled": true, "description": qsTr("Check for new releases without installing automatically.") },
        { "id": "log-level", "category": "advanced", "title": qsTr("Logging level"), "value": String(setting("logLevel", "INFO")), "enabled": true, "description": qsTr("Choose how much diagnostic information is recorded.") },
        { "id": "default-profile", "category": "automation", "title": qsTr("Default compression profile"), "value": String(setting("defaultCompressionProfile", "Auto")), "enabled": true, "description": qsTr("Preselected mode for storage operations.") },

        { "id": "auto-compression", "category": "automation", "title": qsTr("Automatic compression"), "value": String(setting("automaticCompressionMode", "Off")), "enabled": true, "description": qsTr("Choose which launcher events may trigger the guarded workflow.") },
        { "id": "auto-profile", "category": "automation", "title": qsTr("Automatic profile"), "value": String(setting("automaticCompressionProfile", "Auto")), "enabled": String(setting("automaticCompressionMode", "Off")) !== "Off", "description": qsTr("Auto compares measured levels; fixed profiles remain predictable.") },
        { "id": "auto-delay", "category": "automation", "title": qsTr("Safety delay"), "value": qsTr("%1 s").arg(Number(setting("automaticCompressionDelaySeconds", 300))), "enabled": String(setting("automaticCompressionMode", "Off")) !== "Off", "description": qsTr("Wait after the launcher becomes stable.") },
        { "id": "auto-jobs", "category": "automation", "title": qsTr("Maximum parallel jobs"), "value": String(Number(setting("automaticCompressionMaxJobs", 1))), "enabled": String(setting("automaticCompressionMode", "Off")) !== "Off", "description": qsTr("Limit concurrent automatic compression work from one to eight jobs.") },
        { "id": "auto-free", "category": "automation", "title": qsTr("Minimum free space"), "value": qsTr("%1 GiB").arg(Number(setting("automaticCompressionMinFreeGb", 10)).toFixed(1)), "enabled": String(setting("automaticCompressionMode", "Off")) !== "Off", "description": qsTr("Block automatic work below this free-space limit.") },
        { "id": "auto-notify", "category": "automation", "title": qsTr("Completion notifications"), "value": boolLabel(setting("automaticCompressionNotify", true)), "enabled": String(setting("automaticCompressionMode", "Off")) !== "Off", "description": qsTr("Notify when automatic work finishes or is blocked.") },
        { "id": "auto-libraries", "category": "automation", "title": qsTr("Automatic compression libraries"), "value": listLabel("automaticCompressionLibraries"), "enabled": String(setting("automaticCompressionMode", "Off")) !== "Off", "description": qsTr("Restrict automatic work to these existing library paths; separate paths with semicolons.") },
        { "id": "auto-skipped", "category": "automation", "title": qsTr("Skipped Steam AppIDs"), "value": listLabel("automaticCompressionSkippedAppIds"), "enabled": String(setting("automaticCompressionMode", "Off")) !== "Off", "description": qsTr("Never process these Steam games automatically; separate AppIDs with spaces or semicolons.") },

        { "id": "interface", "category": "general", "title": qsTr("Interface mode"), "value": String(setting("controllerMode", "Automatic")), "enabled": true, "description": qsTr("Choose whether GameOpti starts in the desktop or television-friendly interface.") },
        { "id": "swap", "category": "controller", "title": qsTr("Swap Confirm and Back"), "value": boolLabel(setting("swapAcceptBack", false)), "enabled": true, "description": qsTr("Reverse the two primary face-button actions.") },
        { "id": "deadzone", "category": "controller", "title": qsTr("Analog dead zone"), "value": qsTr("%1%").arg(Math.round(Number(setting("analogDeadzone", 0.20)) * 100)), "enabled": true, "description": qsTr("Ignore small stick movement around the center.") },
        { "id": "repeat-delay", "category": "controller", "title": qsTr("Navigation repeat delay"), "value": qsTr("%1 ms").arg(Number(setting("navigationRepeatDelayMs", 350))), "enabled": true, "description": qsTr("Delay before a held direction repeats.") },
        { "id": "repeat-rate", "category": "controller", "title": qsTr("Navigation repeat interval"), "value": qsTr("%1 ms").arg(Number(setting("navigationRepeatRateMs", 110))), "enabled": true, "description": qsTr("Time between repeated navigation steps.") },
        { "id": "cursor", "category": "controller", "title": qsTr("Hide cursor in Couch Mode"), "value": boolLabel(setting("hideCursorInCouchMode", true)), "enabled": true, "description": qsTr("The cursor returns after meaningful mouse movement.") },
        { "id": "fullscreen", "category": "controller", "title": qsTr("Start Couch Mode fullscreen"), "value": boolLabel(setting("startCouchModeFullscreen", true)), "enabled": true, "description": qsTr("Use the whole display when Couch Mode opens.") },
        { "id": "post-launch", "category": "controller", "title": qsTr("After launching a game"), "value": String(setting("postLaunchBehavior", "Minimize")), "enabled": true, "description": qsTr("Choose what the GameOpti window should do.") },

        { "id": "menu-sounds", "category": "audio", "title": qsTr("Enable menu sounds"), "value": boolLabel(setting("couchMenuSoundsEnabled", true)), "enabled": true, "description": qsTr("Play subtle semantic feedback in Couch Mode.") },
        { "id": "menu-volume", "category": "audio", "title": qsTr("Menu sound volume"), "value": qsTr("%1%").arg(effectiveMenuVolume), "enabled": setting("couchMenuSoundsEnabled", true) === true, "description": qsTr("Adjust short navigation and action effects.") },
        { "id": "music", "category": "audio", "title": qsTr("Enable Couch Mode music"), "value": boolLabel(setting("couchMusicEnabled", true)), "enabled": true, "description": qsTr("Play the packaged ambient loop only in Couch Mode.") },
        { "id": "music-volume", "category": "audio", "title": qsTr("Music volume"), "value": qsTr("%1%").arg(effectiveMusicVolume), "enabled": setting("couchMusicEnabled", true) === true, "description": qsTr("Keep the ambient loop below game and Narrator audio.") },

        { "id": "steam-tools", "category": "libraries", "title": qsTr("Show Steam tools and runtimes"), "value": boolLabel(setting("showSteamToolsAndRuntimes", false)), "enabled": true, "description": qsTr("Include Proton, runtimes, SDKs and dedicated servers.") },
        { "id": "steam-paths", "category": "libraries", "title": qsTr("Additional Steam locations"), "value": listLabel("steamInstallationDirectories"), "enabled": true, "description": qsTr("Separate multiple paths with semicolons in the controller keyboard.") },
        { "id": "game-paths", "category": "libraries", "title": qsTr("Local game library directories"), "value": listLabel("libraryDirectories"), "enabled": true, "description": qsTr("Add existing local library roots; use semicolons between paths.") },
        { "id": "forgotten-library", "category": "libraries", "title": qsTr("Restore forgotten Steam library"), "value": ignoredLibraryLabel(), "enabled": stringList("ignoredSteamLibraries").length > 0, "description": qsTr("Use left/right to choose a path, then Confirm to restore it.") },
        { "id": "backup-path", "category": "libraries", "title": qsTr("Backup directory"), "value": String(setting("backupDirectory", "backups")), "enabled": true, "description": qsTr("Edit the backup destination with the controller keyboard.") },
        { "id": "quarantine-path", "category": "libraries", "title": qsTr("Quarantine directory"), "value": String(setting("quarantineDirectory", "quarantine")), "enabled": true, "description": qsTr("Edit the safe quarantine destination with the controller keyboard.") },

        { "id": "cpu-limit", "category": "advanced", "title": qsTr("CPU usage limit"), "value": qsTr("%1%").arg(Number(setting("cpuUsageLimit", 75))), "enabled": true, "description": qsTr("Limit CPU use for managed background work.") },
        { "id": "gpu-limit", "category": "advanced", "title": qsTr("GPU usage limit"), "value": qsTr("%1%").arg(Number(setting("gpuUsageLimit", 75))), "enabled": true, "description": qsTr("Limit GPU use for managed background work.") },
        { "id": "experimental", "category": "advanced", "title": qsTr("Experimental features"), "value": boolLabel(setting("experimentalFeatures", false)), "enabled": true, "description": qsTr("Show unfinished capabilities without bypassing safeguards.") },
        { "id": "desktop", "category": "advanced", "title": qsTr("Switch to Desktop Mode"), "value": qsTr("Always available"), "enabled": true, "description": qsTr("Return to the standard desktop interface.") },
        { "id": "reset", "category": "advanced", "title": qsTr("Reset Couch Mode settings"), "value": qsTr("Safe defaults"), "enabled": true, "description": qsTr("Restore controller and Couch audio defaults without changing game data.") }
    ].concat(narratorRows())
    readonly property var activeRows: rowsForCategory(activeCategoryIndex)
    readonly property int selectedActiveIndex: activeIndexForGlobal(selectedIndex)
    readonly property var selectedRow: rows[selectedIndex] || ({})

    signal backRequested()

    function restoreActiveFocus() {
        if (onScreenKeyboard.opened) {
            onScreenKeyboard.forceActiveFocus()
            onScreenKeyboard.focusSelected()
            return
        }
        forceActiveFocus()
        Qt.callLater(function() {
            if (page.visible && !onScreenKeyboard.opened)
                settingsList.forceActiveFocus()
        })
    }

    function setting(key, fallback) {
        var value = settingsData ? settingsData[key] : undefined
        return value === undefined || value === null || value === "" ? fallback : value
    }

    function boolLabel(value) {
        return value === true ? qsTr("On") : qsTr("Off")
    }

    function languageCode(value) {
        var normalized = String(value || "en").toLowerCase().replace("-", "_")
        if (normalized === "pl" || normalized === "pl_pl" || normalized === "polski" || normalized === "polish")
            return "pl"
        if (normalized === "es" || normalized === "es_es" || normalized === "español" || normalized === "espanol" || normalized === "spanish")
            return "es"
        return "en"
    }

    function languageLabel(value) {
        var code = languageCode(value)
        return code === "pl" ? "Polski" : code === "es" ? "Español" : "English"
    }

    function themeCode(value) {
        var normalized = String(value || "system").toLowerCase()
        return normalized === "dark" || normalized === "light" ? normalized : "system"
    }

    function themeLabel(value) {
        var code = themeCode(value)
        return code === "dark" ? qsTr("Dark") : code === "light" ? qsTr("Light") : qsTr("System")
    }

    function stringList(key) {
        var source = setting(key, []) || []
        return Array.from(source).map(function(value) { return String(value) })
    }

    function parsePathList(value) {
        var parts = String(value || "").split(";")
        var result = []
        for (var index = 0; index < parts.length; ++index) {
            var path = String(parts[index]).trim()
            if (path.length && result.indexOf(path) < 0)
                result.push(path)
        }
        return result
    }

    function listLabel(key) {
        var values = stringList(key)
        return values.length ? values.join("; ") : qsTr("None")
    }

    function ignoredLibraryLabel() {
        var values = stringList("ignoredSteamLibraries")
        if (!values.length)
            return qsTr("None")
        var index = Math.max(0, Math.min(values.length - 1, ignoredLibraryIndex))
        return values[index]
    }

    function parseAppIdList(value) {
        var tokens = String(value || "").trim().split(/[;,\s]+/)
        var result = []
        for (var index = 0; index < tokens.length; ++index) {
            var appId = String(tokens[index]).trim()
            if (!appId.length)
                continue
            if (!/^[0-9]+$/.test(appId) || Number(appId) <= 0)
                return null
            if (result.indexOf(appId) < 0)
                result.push(appId)
        }
        return result
    }

    function narratorComponentName(componentId, fallback) {
        if (componentId === "capture.portal-pipewire")
            return qsTr("Screen capture portal")
        if (componentId === "ocr.english-local")
            return qsTr("English subtitle OCR")
        if (componentId === "ocr.polish-local")
            return qsTr("Polish subtitle OCR")
        if (componentId === "translation.opus-en-pl")
            return qsTr("English to Polish translation")
        if (componentId === "tts.polish-voice")
            return qsTr("Polish voice - Gosia")
        if (componentId === "tts.polish-bass")
            return qsTr("Polish voice - Bass")
        if (componentId === "audio.qt-pcm")
            return qsTr("Narrator audio output")
        return String(fallback || componentId)
    }

    function narratorComponentDescription(code) {
        if (code === "capture_runtime")
            return qsTr("Capture support supplied by the application runtime.")
        if (code === "ocr_model_required")
            return qsTr("Local English subtitle recognition model.")
        if (code === "polish_ocr_model_required")
            return qsTr("Local Polish subtitle recognition model.")
        if (code === "translation_model_required")
            return qsTr("Local CPU translation model for English subtitles.")
        if (code === "polish_voice_required")
            return qsTr("Local Polish speech voice used by per-game Narrator settings.")
        if (code === "audio_runtime")
            return qsTr("Audio playback supplied by the application runtime.")
        return qsTr("Local Narrator component.")
    }

    function narratorComponentState(component) {
        var state = String(component && component.state || "")
        if (state === "available")
            return qsTr("Ready")
        if (state === "not_installed")
            return qsTr("Not installed")
        if (state === "installing")
            return qsTr("Installing…")
        if (state === "update_available")
            return qsTr("Update available")
        if (state === "error")
            return qsTr("Needs attention")
        if (state === "unsupported")
            return qsTr("Unavailable on this system")
        return qsTr("Unknown")
    }

    function narratorGlobalVoices() {
        var source = narratorGlobalData && narratorGlobalData.voices
                ? Array.from(narratorGlobalData.voices) : []
        return source.filter(function(voice) {
            return voice && (voice.available === true || voice.installed === true)
        })
    }

    function narratorGlobalVoiceLabel() {
        var voices = narratorGlobalVoices()
        var selected = String(narratorGlobalData.voiceId || "")
        for (var index = 0; index < voices.length; ++index) {
            if (String(voices[index].id || "") === selected)
                return String(voices[index].name || voices[index].id)
        }
        return voices.length ? String(voices[0].name || voices[0].id)
                             : qsTr("No installed voice")
    }

    function narratorGlobalRegionLabel() {
        var region = narratorGlobalData.subtitleRegion || ({})
        var y = Number(region.y === undefined ? 0.62 : region.y)
        var height = Number(region.height === undefined ? 0.30 : region.height)
        if (y <= 0.08 && height >= 0.82)
            return qsTr("Full frame")
        if (y <= 0.45)
            return qsTr("Lower half")
        return qsTr("Bottom subtitles")
    }

    function narratorRows() {
        var components = Array.from(page.narratorComponents || [])
        var articulation = narratorGlobalData.noiseScale !== null
                && narratorGlobalData.noiseScale !== undefined
                || narratorGlobalData.noiseWScale !== null
                && narratorGlobalData.noiseWScale !== undefined
        var profiles = Array.from(narratorGlobalData.translationProfiles || [])
        var result = [
            { "id": "narrator-global-enabled", "category": "narrator", "title": qsTr("Enable Narrator by default"), "value": boolLabel(narratorGlobalData.enabled === true), "enabled": narratorGlobalData.success === true, "description": qsTr("New per-game profiles inherit this default; existing game profiles remain unchanged.") },
            { "id": "narrator-global-language", "category": "narrator", "title": qsTr("Subtitle recognition language"), "value": String(narratorGlobalData.subtitleLanguageMode || "english_to_polish") === "polish" ? qsTr("Polish subtitles") : qsTr("English → Polish"), "enabled": narratorGlobalData.success === true, "description": qsTr("Recognize Polish directly or translate recognized English subtitles to Polish.") },
            { "id": "narrator-global-source", "category": "narrator", "title": qsTr("Recognition source"), "value": String(narratorGlobalData.sourceMode || "auto") === "ocr" ? qsTr("OCR only") : qsTr("Automatic"), "enabled": narratorGlobalData.success === true, "description": qsTr("Use automatic source selection or always use screen OCR.") },
            { "id": "narrator-global-capture", "category": "narrator", "title": qsTr("Capture source"), "value": String(narratorGlobalData.captureSource || "window") === "monitor" ? qsTr("Monitor") : qsTr("Game window"), "enabled": narratorGlobalData.success === true, "description": qsTr("Choose whether new profiles capture a game window or the full monitor.") },
            { "id": "narrator-global-translation", "category": "narrator", "title": qsTr("Translation profile"), "value": String(narratorGlobalData.translationProfileId || qsTr("Automatic")), "enabled": narratorGlobalData.success === true && String(narratorGlobalData.subtitleLanguageMode || "english_to_polish") !== "polish" && profiles.length > 0, "description": qsTr("Select the installed local translation profile.") },
            { "id": "narrator-global-voice", "category": "narrator", "title": qsTr("Speech voice"), "value": narratorGlobalVoiceLabel(), "enabled": narratorGlobalData.success === true && narratorGlobalVoices().length > 0, "description": qsTr("Select the default installed voice for Polish speech.") },
            { "id": "narrator-global-volume", "category": "narrator", "title": qsTr("Speech volume"), "value": qsTr("%1%").arg(Math.round(Number(narratorGlobalData.volume === undefined ? 0.85 : narratorGlobalData.volume) * 100)), "enabled": narratorGlobalData.success === true, "description": qsTr("Set the default Narrator playback volume.") },
            { "id": "narrator-global-rate", "category": "narrator", "title": qsTr("Speech rate"), "value": qsTr("%1×").arg(Number(narratorGlobalData.speechRate || 1).toFixed(2)), "enabled": narratorGlobalData.success === true, "description": qsTr("Adjust default speech speed from 0.5× to 2.0×.") },
            { "id": "narrator-global-sampling", "category": "narrator", "title": qsTr("Capture sampling rate"), "value": qsTr("%1 Hz").arg(Number(narratorGlobalData.captureSamplingHz || 6).toFixed(1)), "enabled": narratorGlobalData.success === true, "description": qsTr("Choose how often the subtitle region is sampled.") },
            { "id": "narrator-global-change", "category": "narrator", "title": qsTr("Visual change threshold"), "value": qsTr("%1%").arg(Math.round(Number(narratorGlobalData.visualChangeThreshold || 0.08) * 100)), "enabled": narratorGlobalData.success === true, "description": qsTr("Ignore frames whose subtitle region changed less than this amount.") },
            { "id": "narrator-global-stabilization", "category": "narrator", "title": qsTr("Subtitle stabilization"), "value": qsTr("%1 ms").arg(Number(narratorGlobalData.stabilizationMs || 240)), "enabled": narratorGlobalData.success === true, "description": qsTr("Wait for subtitles to settle before recognition.") },
            { "id": "narrator-global-confidence", "category": "narrator", "title": qsTr("Minimum OCR confidence"), "value": qsTr("%1%").arg(Math.round(Number(narratorGlobalData.ocrMinConfidence || 0.62) * 100)), "enabled": narratorGlobalData.success === true, "description": qsTr("Reject recognition results below this confidence.") },
            { "id": "narrator-global-cooldown", "category": "narrator", "title": qsTr("Duplicate subtitle cooldown"), "value": qsTr("%1 s").arg((Number(narratorGlobalData.duplicateCooldownMs || 4500) / 1000).toFixed(1)), "enabled": narratorGlobalData.success === true, "description": qsTr("Delay before the same subtitle may be spoken again.") },
            { "id": "narrator-global-articulation", "category": "narrator", "title": qsTr("Voice articulation overrides"), "value": articulation ? qsTr("Custom") : qsTr("Voice defaults"), "enabled": narratorGlobalData.success === true, "description": qsTr("Use each voice's tuned defaults or expose custom Piper articulation values.") },
            { "id": "narrator-global-noise-w", "category": "narrator", "title": qsTr("Phoneme width variation"), "value": Number(narratorGlobalData.noiseWScale === null || narratorGlobalData.noiseWScale === undefined ? 0.8 : narratorGlobalData.noiseWScale).toFixed(3), "enabled": narratorGlobalData.success === true && articulation, "description": qsTr("Advanced Piper timing variation for newly created profiles.") },
            { "id": "narrator-global-noise", "category": "narrator", "title": qsTr("Voice variation"), "value": Number(narratorGlobalData.noiseScale === null || narratorGlobalData.noiseScale === undefined ? 0.667 : narratorGlobalData.noiseScale).toFixed(3), "enabled": narratorGlobalData.success === true && articulation, "description": qsTr("Advanced Piper voice variation for newly created profiles.") },
            { "id": "narrator-global-region", "category": "narrator", "title": qsTr("Default subtitle region"), "value": narratorGlobalRegionLabel(), "enabled": narratorGlobalData.success === true, "description": qsTr("Cycle a controller-friendly default capture region; games can override it individually.") },
            {
                "id": "narrator-overview",
                "category": "narrator",
                "title": qsTr("Narrator components"),
                "value": qsTr("%1 components").arg(components.length),
                "enabled": true,
                "description": qsTr("Install and maintain the local capture, OCR, translation and speech building blocks used by each game.")
            }
        ]
        for (var index = 0; index < components.length; ++index) {
            var component = components[index] || ({})
            var componentId = String(component.componentId || "")
            if (!componentId.length)
                continue
            var state = String(component.state || "")
            var action = ""
            var actionLabel = narratorComponentState(component)
            if (state === "update_available" && component.canUpdate === true) {
                action = "update"
                actionLabel = qsTr("Update")
            } else if ((state === "not_installed" || state === "error")
                       && component.canInstall === true) {
                action = "install"
                actionLabel = qsTr("Install")
            }
            var title = narratorComponentName(componentId, component.name)
            result.push({
                "id": "narrator-component-" + componentId,
                "category": "narrator",
                "title": title,
                "value": actionLabel,
                "enabled": true,
                "componentId": componentId,
                "componentAction": action,
                "description": narratorComponentDescription(String(component.descriptionCode || ""))
            })
            if (component.canRemove === true) {
                result.push({
                    "id": "narrator-remove-" + componentId,
                    "category": "narrator",
                    "title": qsTr("Remove %1").arg(title),
                    "value": qsTr("Remove"),
                    "enabled": true,
                    "componentId": componentId,
                    "componentAction": "remove",
                    "description": qsTr("Remove only the component files managed by GameOpti.")
                })
            }
        }
        return result
    }

    function cycle(values, current, delta) {
        var index = values.indexOf(String(current))
        if (index < 0)
            index = 0
        return values[(index + delta + values.length) % values.length]
    }

    function categoryIndexForId(categoryId) {
        for (var index = 0; index < categories.length; ++index) {
            if (categories[index].id === categoryId)
                return index
        }
        return 0
    }

    function rowsForCategory(categoryIndex) {
        if (!categories[categoryIndex])
            return []
        var categoryId = categories[categoryIndex].id
        var result = []
        for (var index = 0; index < rows.length; ++index) {
            if (rows[index].category === categoryId)
                result.push(rows[index])
        }
        return result
    }

    function globalIndexForId(rowId) {
        for (var index = 0; index < rows.length; ++index) {
            if (rows[index].id === rowId)
                return index
        }
        return -1
    }

    function activeIndexForGlobal(globalIndex) {
        if (!rows[globalIndex])
            return -1
        for (var index = 0; index < activeRows.length; ++index) {
            if (activeRows[index].id === rows[globalIndex].id)
                return index
        }
        return -1
    }

    function firstEnabledIndex(categoryIndex) {
        var categoryRows = rowsForCategory(categoryIndex)
        for (var index = 0; index < categoryRows.length; ++index) {
            if (categoryRows[index].enabled)
                return globalIndexForId(categoryRows[index].id)
        }
        return -1
    }

    function playSemanticSound(kind) {
        if (controller && controller.playCouchSound && String(kind || "").length)
            controller.playCouchSound(String(kind))
    }

    function selectIndex(index, rememberFocus) {
        if (index < 0 || index >= rows.length || !rows[index].enabled)
            return false
        var changed = selectedIndex !== index
        selectedIndex = index
        activeCategoryIndex = categoryIndexForId(rows[index].category)
        Qt.callLater(function() {
            if (settingsList && selectedActiveIndex >= 0)
                settingsList.positionViewAtIndex(selectedActiveIndex, ListView.Contain)
        })
        if (rememberFocus && navigation)
            navigation.rememberFocus("settings", rows[index].id, index)
        return changed
    }

    function selectCategory(index) {
        var target = Math.max(0, Math.min(categories.length - 1, index))
        if (target === activeCategoryIndex && rows[selectedIndex]
                && rows[selectedIndex].category === categories[target].id
                && rows[selectedIndex].enabled)
            return false
        var rowIndex = firstEnabledIndex(target)
        return rowIndex >= 0 ? selectIndex(rowIndex, true) : false
    }

    function changeCategory(delta) {
        var changed = selectCategory(Math.max(0, Math.min(categories.length - 1,
                                                          activeCategoryIndex + delta)))
        if (changed)
            playSemanticSound("navigate")
        return changed
    }

    function reconcileSelection() {
        var ignored = stringList("ignoredSteamLibraries")
        ignoredLibraryIndex = ignored.length
                ? Math.max(0, Math.min(ignored.length - 1, ignoredLibraryIndex)) : 0
        if (rows[selectedIndex] && rows[selectedIndex].enabled
                && rows[selectedIndex].category === categories[activeCategoryIndex].id)
            return
        var categoryRows = rowsForCategory(activeCategoryIndex)
        var preferred = Math.max(0, activeIndexForGlobal(selectedIndex))
        for (var distance = 0; distance < categoryRows.length; ++distance) {
            var before = preferred - distance
            if (before >= 0 && categoryRows[before].enabled) {
                selectIndex(globalIndexForId(categoryRows[before].id), true)
                return
            }
            var after = preferred + distance
            if (after < categoryRows.length && categoryRows[after].enabled) {
                selectIndex(globalIndexForId(categoryRows[after].id), true)
                return
            }
        }
        selectCategory(activeCategoryIndex)
    }

    function clamp(value, minimum, maximum) {
        return Math.max(minimum, Math.min(maximum, value))
    }

    function valuesEqual(left, right) {
        if (typeof left === "number" || typeof right === "number")
            return Number(left) === Number(right)
        return left === right
    }

    function saveAdjusted(key, value) {
        if (!controller)
            return false
        if (pendingMenuVolume >= 0 || pendingMusicVolume >= 0)
            commitPendingVolumes()
        var current = settingsData ? settingsData[key] : undefined
        if (current !== undefined && valuesEqual(current, value))
            return false
        var saved = Boolean(controller.saveSetting(key, value))
        playSemanticSound(saved ? "adjust" : "error")
        return saved
    }

    function loadNarratorGlobal() {
        if (controller && controller.getNarratorGlobalSettings)
            narratorGlobalData = controller.getNarratorGlobalSettings() || ({})
        else
            narratorGlobalData = ({})
    }

    function saveNarratorGlobal(values) {
        if (!controller || !controller.saveNarratorGlobalSettings)
            return false
        var saved = Boolean(controller.saveNarratorGlobalSettings(values || ({})))
        if (saved)
            loadNarratorGlobal()
        playSemanticSound(saved ? "adjust" : "error")
        return saved
    }

    function changeNarratorGlobal(id, delta) {
        if (id === "narrator-global-enabled")
            return saveNarratorGlobal({ "enabled": narratorGlobalData.enabled !== true })
        if (id === "narrator-global-language")
            return saveNarratorGlobal({ "subtitleLanguageMode": String(narratorGlobalData.subtitleLanguageMode || "english_to_polish") === "polish" ? "english_to_polish" : "polish" })
        if (id === "narrator-global-source")
            return saveNarratorGlobal({ "sourceMode": String(narratorGlobalData.sourceMode || "auto") === "ocr" ? "auto" : "ocr" })
        if (id === "narrator-global-capture")
            return saveNarratorGlobal({ "captureSource": String(narratorGlobalData.captureSource || "window") === "monitor" ? "window" : "monitor" })
        if (id === "narrator-global-translation") {
            var profiles = Array.from(narratorGlobalData.translationProfiles || [])
            var profileIds = profiles.map(function(profile) { return String(profile.id || "") })
            if (!profileIds.length) return false
            return saveNarratorGlobal({ "translationProfileId": cycle(profileIds, String(narratorGlobalData.translationProfileId || profileIds[0]), delta) })
        }
        if (id === "narrator-global-voice") {
            var voices = narratorGlobalVoices()
            var voiceIds = voices.map(function(voice) { return String(voice.id || "") })
            if (!voiceIds.length) return false
            return saveNarratorGlobal({ "voiceId": cycle(voiceIds, String(narratorGlobalData.voiceId || voiceIds[0]), delta) })
        }
        if (id === "narrator-global-volume")
            return saveNarratorGlobal({ "volume": clamp(Number(narratorGlobalData.volume === undefined ? 0.85 : narratorGlobalData.volume) + delta * 0.05, 0, 1) })
        if (id === "narrator-global-rate")
            return saveNarratorGlobal({ "speechRate": clamp(Number(narratorGlobalData.speechRate || 1) + delta * 0.05, 0.5, 2) })
        if (id === "narrator-global-sampling")
            return saveNarratorGlobal({ "captureSamplingHz": clamp(Number(narratorGlobalData.captureSamplingHz || 6) + delta * 0.5, 1, 10) })
        if (id === "narrator-global-change")
            return saveNarratorGlobal({ "visualChangeThreshold": clamp(Number(narratorGlobalData.visualChangeThreshold || 0.08) + delta * 0.01, 0.001, 1) })
        if (id === "narrator-global-stabilization")
            return saveNarratorGlobal({ "stabilizationMs": clamp(Number(narratorGlobalData.stabilizationMs || 240) + delta * 50, 50, 3000) })
        if (id === "narrator-global-confidence")
            return saveNarratorGlobal({ "ocrMinConfidence": clamp(Number(narratorGlobalData.ocrMinConfidence || 0.62) + delta * 0.05, 0, 1) })
        if (id === "narrator-global-cooldown")
            return saveNarratorGlobal({ "duplicateCooldownMs": clamp(Number(narratorGlobalData.duplicateCooldownMs || 4500) + delta * 250, 250, 60000) })
        if (id === "narrator-global-articulation") {
            var overridden = narratorGlobalData.noiseScale !== null
                    && narratorGlobalData.noiseScale !== undefined
                    || narratorGlobalData.noiseWScale !== null
                    && narratorGlobalData.noiseWScale !== undefined
            return saveNarratorGlobal(overridden
                    ? { "noiseScale": null, "noiseWScale": null }
                    : { "noiseScale": 0.667, "noiseWScale": 0.8 })
        }
        if (id === "narrator-global-noise-w")
            return saveNarratorGlobal({ "noiseWScale": clamp(Number(narratorGlobalData.noiseWScale || 0.8) + delta * 0.025, 0, 2) })
        if (id === "narrator-global-noise")
            return saveNarratorGlobal({ "noiseScale": clamp(Number(narratorGlobalData.noiseScale || 0.667) + delta * 0.025, 0, 2) })
        if (id === "narrator-global-region") {
            var regions = [
                { "x": 0.05, "y": 0.62, "width": 0.90, "height": 0.30 },
                { "x": 0.05, "y": 0.45, "width": 0.90, "height": 0.50 },
                { "x": 0.05, "y": 0.05, "width": 0.90, "height": 0.90 }
            ]
            var currentRegion = narratorGlobalRegionLabel()
            var currentIndex = currentRegion === qsTr("Full frame") ? 2
                    : currentRegion === qsTr("Lower half") ? 1 : 0
            return saveNarratorGlobal({ "subtitleRegion": regions[(currentIndex + delta + regions.length) % regions.length] })
        }
        return false
    }

    function runNarratorComponentAction(row) {
        if (!controller || !row || !row.componentAction)
            return false
        var action = String(row.componentAction)
        var componentId = String(row.componentId || "")
        var started = false
        if (action === "install" && controller.installNarratorComponent)
            started = Boolean(controller.installNarratorComponent(componentId))
        else if (action === "update" && controller.updateNarratorComponent)
            started = Boolean(controller.updateNarratorComponent(componentId))
        else if (action === "remove" && controller.removeNarratorComponent)
            started = Boolean(controller.removeNarratorComponent(componentId))
        playSemanticSound(started ? "confirm" : "error")
        return started
    }

    function previewVolume(key, value) {
        if (!controller || !controller.previewCouchAudioSetting)
            return false
        var normalized = clamp(Math.round(Number(value)), 0, 100)
        var current = key === "couchMenuSoundsVolume"
                ? effectiveMenuVolume : effectiveMusicVolume
        if (normalized === current)
            return false
        if (!controller.previewCouchAudioSetting(key, normalized)) {
            playSemanticSound("error")
            return false
        }
        if (key === "couchMenuSoundsVolume")
            pendingMenuVolume = normalized
        else
            pendingMusicVolume = normalized
        volumePersistenceTimer.restart()
        playSemanticSound("adjust")
        return true
    }

    function commitPendingVolumes() {
        volumePersistenceTimer.stop()
        var menuValue = pendingMenuVolume
        var musicValue = pendingMusicVolume
        pendingMenuVolume = -1
        pendingMusicVolume = -1
        var succeeded = true
        if (menuValue >= 0)
            succeeded = Boolean(controller && controller.saveSetting(
                                    "couchMenuSoundsVolume", menuValue)) && succeeded
        if (musicValue >= 0)
            succeeded = Boolean(controller && controller.saveSetting(
                                    "couchMusicVolume", musicValue)) && succeeded
        if (!succeeded)
            playSemanticSound("error")
        return succeeded
    }

    function openKeyboard(settingId, value, heading) {
        commitPendingVolumes()
        editingSettingId = settingId
        if (navigation)
            navigation.openModal("couch-keyboard", "keyboard-key")
        onScreenKeyboard.open(value, heading)
        playSemanticSound("open")
    }

    function closeKeyboard() {
        if (onScreenKeyboard.opened)
            onScreenKeyboard.close(false)
    }

    function change(delta) {
        if (!controller || !rows[selectedIndex] || !rows[selectedIndex].enabled) {
            playSemanticSound("error")
            return false
        }
        var row = rows[selectedIndex]
        var id = row.id
        if (id.indexOf("narrator-global-") === 0)
            return changeNarratorGlobal(id, delta)
        if (row.componentAction)
            return runNarratorComponentAction(row)
        if (id === "narrator-overview") {
            playSemanticSound("confirm")
            return true
        }
        if (id === "language") {
            commitPendingVolumes()
            var language = cycle(["en", "pl", "es"], languageCode(setting("language", "en")), delta)
            var languageSaved = controller.saveSetting("language", language)
            // translationManager is an application context property.
            // qmllint disable unqualified
            if (languageSaved
                    && typeof translationManager !== "undefined"
                    && translationManager && translationManager.setLanguage)
                translationManager.setLanguage(language)
            // qmllint enable unqualified
            playSemanticSound(languageSaved ? "adjust" : "error")
        } else if (id === "appearance")
            saveAdjusted("themeMode", cycle(["system", "dark", "light"], themeCode(setting("themeMode", "system")), delta))
        else if (id === "automatic-updates")
            saveAdjusted("automaticUpdates", setting("automaticUpdates", true) !== true)
        else if (id === "log-level")
            saveAdjusted("logLevel", cycle(["DEBUG", "INFO", "WARNING", "ERROR"], setting("logLevel", "INFO"), delta))
        else if (id === "default-profile")
            saveAdjusted("defaultCompressionProfile", cycle(["Fast", "Balanced", "Maximum", "Auto"], setting("defaultCompressionProfile", "Auto"), delta))
        else if (id === "auto-compression")
            saveAdjusted("automaticCompressionMode", cycle(["Off", "After new game installation", "After game update", "After installation and update"], setting("automaticCompressionMode", "Off"), delta))
        else if (id === "auto-profile")
            saveAdjusted("automaticCompressionProfile", cycle(["Fast", "Balanced", "Maximum", "Auto"], setting("automaticCompressionProfile", "Auto"), delta))
        else if (id === "auto-delay")
            saveAdjusted("automaticCompressionDelaySeconds", clamp(Number(setting("automaticCompressionDelaySeconds", 300)) + delta * 30, 0, 86400))
        else if (id === "auto-jobs")
            saveAdjusted("automaticCompressionMaxJobs", clamp(Number(setting("automaticCompressionMaxJobs", 1)) + delta, 1, 8))
        else if (id === "auto-free")
            saveAdjusted("automaticCompressionMinFreeGb", clamp(Number(setting("automaticCompressionMinFreeGb", 10)) + delta, 0, 1000000))
        else if (id === "auto-notify")
            saveAdjusted("automaticCompressionNotify", setting("automaticCompressionNotify", true) !== true)
        else if (id === "auto-libraries")
            openKeyboard(id, stringList("automaticCompressionLibraries").join("; "), rows[selectedIndex].title)
        else if (id === "auto-skipped")
            openKeyboard(id, stringList("automaticCompressionSkippedAppIds").join("; "), rows[selectedIndex].title)
        else if (id === "interface")
            saveAdjusted("controllerMode", cycle(["Automatic", "Desktop only", "Couch only"], setting("controllerMode", "Automatic"), delta))
        else if (id === "swap")
            saveAdjusted("swapAcceptBack", setting("swapAcceptBack", false) !== true)
        else if (id === "deadzone")
            saveAdjusted("analogDeadzone", clamp(Number(setting("analogDeadzone", 0.20)) + delta * 0.05, 0.05, 0.75))
        else if (id === "repeat-delay")
            saveAdjusted("navigationRepeatDelayMs", clamp(Number(setting("navigationRepeatDelayMs", 350)) + delta * 50, 150, 1500))
        else if (id === "repeat-rate")
            saveAdjusted("navigationRepeatRateMs", clamp(Number(setting("navigationRepeatRateMs", 110)) + delta * 10, 50, 500))
        else if (id === "cursor")
            saveAdjusted("hideCursorInCouchMode", setting("hideCursorInCouchMode", true) !== true)
        else if (id === "fullscreen")
            saveAdjusted("startCouchModeFullscreen", setting("startCouchModeFullscreen", true) !== true)
        else if (id === "post-launch")
            saveAdjusted("postLaunchBehavior", cycle(["Minimize", "Stay open", "Close launcher"], setting("postLaunchBehavior", "Minimize"), delta))
        else if (id === "menu-sounds")
            saveAdjusted("couchMenuSoundsEnabled", setting("couchMenuSoundsEnabled", true) !== true)
        else if (id === "menu-volume")
            previewVolume("couchMenuSoundsVolume", effectiveMenuVolume + delta * 5)
        else if (id === "music")
            saveAdjusted("couchMusicEnabled", setting("couchMusicEnabled", true) !== true)
        else if (id === "music-volume")
            previewVolume("couchMusicVolume", effectiveMusicVolume + delta * 5)
        else if (id === "steam-tools")
            saveAdjusted("showSteamToolsAndRuntimes", setting("showSteamToolsAndRuntimes", false) !== true)
        else if (id === "steam-paths")
            openKeyboard(id, stringList("steamInstallationDirectories").join("; "), rows[selectedIndex].title)
        else if (id === "game-paths")
            openKeyboard(id, stringList("libraryDirectories").join("; "), rows[selectedIndex].title)
        else if (id === "forgotten-library") {
            var ignored = stringList("ignoredSteamLibraries")
            if (ignored.length) {
                var previousIgnoredIndex = ignoredLibraryIndex
                ignoredLibraryIndex = (ignoredLibraryIndex + delta + ignored.length) % ignored.length
                if (ignoredLibraryIndex !== previousIgnoredIndex)
                    playSemanticSound("adjust")
            }
        } else if (id === "cpu-limit")
            saveAdjusted("cpuUsageLimit", clamp(Number(setting("cpuUsageLimit", 75)) + delta * 5, 1, 100))
        else if (id === "gpu-limit")
            saveAdjusted("gpuUsageLimit", clamp(Number(setting("gpuUsageLimit", 75)) + delta * 5, 1, 100))
        else if (id === "experimental")
            saveAdjusted("experimentalFeatures", setting("experimentalFeatures", false) !== true)
        else if (id === "backup-path" || id === "quarantine-path")
            openKeyboard(id, rows[selectedIndex].value, rows[selectedIndex].title)
    }

    function activate() {
        if (!rows[selectedIndex] || !rows[selectedIndex].enabled || !controller) {
            playSemanticSound("error")
            return false
        }
        var row = rows[selectedIndex]
        var id = row.id
        if (row.componentAction)
            return runNarratorComponentAction(row)
        if (id === "narrator-overview") {
            playSemanticSound("confirm")
            return true
        }
        if (id === "desktop") {
            commitPendingVolumes()
            controller.setInterfaceMode("desktop")
            playSemanticSound("confirm")
            return true
        }
        if (id === "forgotten-library") {
            var ignored = stringList("ignoredSteamLibraries")
            var restoreIndex = Math.max(0, Math.min(ignored.length - 1, ignoredLibraryIndex))
            var restored = Boolean(ignored.length && controller.restoreIgnoredLibrary
                    && controller.restoreIgnoredLibrary(ignored[restoreIndex]))
            if (restored) {
                ignoredLibraryIndex = Math.max(0, restoreIndex - 1)
                Qt.callLater(reconcileSelection)
            }
            playSemanticSound(restored ? "confirm" : "error")
            return restored
        }
        if (id === "reset") {
            commitPendingVolumes()
            var saved = true
            saved = Boolean(controller.saveSetting("controllerMode", "Automatic")) && saved
            saved = Boolean(controller.saveSetting("swapAcceptBack", false)) && saved
            saved = Boolean(controller.saveSetting("analogDeadzone", 0.20)) && saved
            saved = Boolean(controller.saveSetting("navigationRepeatDelayMs", 350)) && saved
            saved = Boolean(controller.saveSetting("navigationRepeatRateMs", 110)) && saved
            saved = Boolean(controller.saveSetting("hideCursorInCouchMode", true)) && saved
            saved = Boolean(controller.saveSetting("startCouchModeFullscreen", true)) && saved
            saved = Boolean(controller.saveSetting("postLaunchBehavior", "Minimize")) && saved
            saved = Boolean(controller.saveSetting("couchMenuSoundsEnabled", true)) && saved
            saved = Boolean(controller.saveSetting("couchMenuSoundsVolume", 40)) && saved
            saved = Boolean(controller.saveSetting("couchMusicEnabled", true)) && saved
            saved = Boolean(controller.saveSetting("couchMusicVolume", 20)) && saved
            playSemanticSound(saved ? "confirm" : "error")
            return saved
        }
        change(1)
        return true
    }

    function move(delta) {
        if (!activeRows.length)
            return false
        var current = selectedActiveIndex >= 0 ? selectedActiveIndex : 0
        var direction = delta < 0 ? -1 : 1
        var candidate = Math.max(0, Math.min(activeRows.length - 1, current + delta))
        while (candidate >= 0 && candidate < activeRows.length) {
            if (activeRows[candidate].enabled)
                return selectIndex(globalIndexForId(activeRows[candidate].id), true)
            candidate += direction
        }
        return false
    }

    function restoreFocus() {
        if (!navigation) {
            reconcileSelection()
            return
        }
        var rememberedIndex = globalIndexForId(String(navigation.focusedId || ""))
        if (rememberedIndex >= 0 && rows[rememberedIndex].enabled)
            selectIndex(rememberedIndex, false)
        else
            reconcileSelection()
    }

    function handleAction(action) {
        if (action === "Accept") action = "Confirm"
        else if (action === "PageLeft" || action === "PreviousSection") action = "PreviousTab"
        else if (action === "PageRight" || action === "NextSection") action = "NextTab"
        if (onScreenKeyboard.opened) {
            onScreenKeyboard.handleAction(action)
            return
        }
        if (action === "Back") {
            commitPendingVolumes()
            backRequested()
            playSemanticSound("back")
        } else if (action === "NavigateUp") {
            if (move(-1)) playSemanticSound("navigate")
        } else if (action === "NavigateDown") {
            if (move(1)) playSemanticSound("navigate")
        } else if (action === "NavigateLeft")
            change(-1)
        else if (action === "NavigateRight")
            change(1)
        else if (action === "Confirm")
            activate()
        else if (action === "PreviousTab")
            changeCategory(-1)
        else if (action === "NextTab")
            changeCategory(1)
        else if (action === "PageUp") {
            if (move(-5)) playSemanticSound("navigate")
        } else if (action === "PageDown") {
            if (move(5)) playSemanticSound("navigate")
        }
    }

    focus: visible
    Component.onCompleted: {
        loadNarratorGlobal()
        restoreFocus()
        restoreActiveFocus()
    }
    onSettingsDataChanged: Qt.callLater(reconcileSelection)
    onActiveRowsChanged: Qt.callLater(reconcileSelection)
    onVisibleChanged: {
        if (visible) {
            loadNarratorGlobal()
            Qt.callLater(restoreFocus)
            restoreActiveFocus()
        } else {
            commitPendingVolumes()
        }
    }

    Timer {
        id: volumePersistenceTimer
        interval: 450
        repeat: false
        onTriggered: page.commitPendingVolumes()
    }

    Rectangle {
        anchors.fill: parent
        color: App.Theme.background
        gradient: Gradient {
            GradientStop { position: 0.0; color: App.Theme.dark ? "#111B28" : "#F8FAFD" }
            GradientStop { position: 0.65; color: App.Theme.background }
            GradientStop { position: 1.0; color: App.Theme.dark ? "#080D14" : "#EAF0F7" }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: 64 * page.couchScale
        anchors.rightMargin: 64 * page.couchScale
        anchors.topMargin: 112 * page.couchScale
        anchors.bottomMargin: 90 * page.couchScale
        spacing: 22 * page.couchScale

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 5 * page.couchScale

            Label {
                Layout.fillWidth: true
                text: qsTr("Couch Mode settings")
                color: App.Theme.text
                font.pixelSize: 42 * page.couchScale
                font.weight: Font.Bold
            }
            Label {
                Layout.fillWidth: true
                text: qsTr("Use the controller to choose a category and adjust its options.")
                color: App.Theme.textSecondary
                font.pixelSize: 18 * page.couchScale
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 22 * page.couchScale

            Rectangle {
                Layout.preferredWidth: 310 * page.couchScale
                Layout.fillHeight: true
                radius: 24 * page.couchScale
                color: App.Theme.dark ? "#D91A2432" : "#ECFFFFFF"
                border.width: 1
                border.color: App.Theme.border

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 18 * page.couchScale
                    spacing: 12 * page.couchScale

                    Label {
                        Layout.fillWidth: true
                        Layout.leftMargin: 12 * page.couchScale
                        text: qsTr("Categories")
                        color: App.Theme.textSecondary
                        font.pixelSize: 17 * page.couchScale
                        font.weight: Font.DemiBold
                    }

                    Repeater {
                        model: page.categories
                        delegate: Rectangle {
                            id: categoryDelegate
                            required property var modelData
                            required property int index
                            readonly property bool active: page.activeCategoryIndex === index
                            Layout.fillWidth: true
                            Layout.preferredHeight: 84 * page.couchScale
                            radius: 16 * page.couchScale
                            color: active ? App.Theme.surfaceSelected : "transparent"
                            border.width: active ? 2 * page.couchScale : 0
                            border.color: active ? App.Theme.accent : "transparent"

                            Rectangle {
                                anchors.left: parent.left
                                anchors.leftMargin: 10 * page.couchScale
                                anchors.verticalCenter: parent.verticalCenter
                                width: 5 * page.couchScale
                                height: 42 * page.couchScale
                                radius: width / 2
                                color: App.Theme.accent
                                visible: categoryDelegate.active
                            }

                            ColumnLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 28 * page.couchScale
                                anchors.rightMargin: 14 * page.couchScale
                                spacing: 2 * page.couchScale
                                Label {
                                    Layout.fillWidth: true
                                    text: categoryDelegate.modelData.title
                                    color: categoryDelegate.active ? App.Theme.text : App.Theme.textSecondary
                                    font.pixelSize: 21 * page.couchScale
                                    font.weight: categoryDelegate.active ? Font.Bold : Font.DemiBold
                                    elide: Text.ElideRight
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: categoryDelegate.modelData.subtitle
                                    color: categoryDelegate.active ? App.Theme.textSecondary : App.Theme.textMuted
                                    font.pixelSize: 15 * page.couchScale
                                    elide: Text.ElideRight
                                }
                            }

                            MouseArea {
                                anchors.fill: parent
                                onClicked: page.selectCategory(categoryDelegate.index)
                            }
                        }
                    }

                    Item { Layout.fillHeight: true }

                    Label {
                        Layout.fillWidth: true
                        Layout.leftMargin: 12 * page.couchScale
                        Layout.rightMargin: 12 * page.couchScale
                        text: qsTr("Use LB and RB to change category")
                        color: App.Theme.textMuted
                        font.pixelSize: 15 * page.couchScale
                        wrapMode: Text.WordWrap
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                radius: 24 * page.couchScale
                color: App.Theme.dark ? "#E6151D29" : "#F4FFFFFF"
                border.width: 1
                border.color: App.Theme.border

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 22 * page.couchScale
                    spacing: 14 * page.couchScale

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2 * page.couchScale
                        Label {
                            Layout.fillWidth: true
                            text: page.categories[page.activeCategoryIndex]
                                  ? page.categories[page.activeCategoryIndex].title : ""
                            color: App.Theme.text
                            font.pixelSize: 30 * page.couchScale
                            font.weight: Font.Bold
                        }
                        Label {
                            Layout.fillWidth: true
                            text: qsTr("Use left and right to change the selected value.")
                            color: App.Theme.textSecondary
                            font.pixelSize: 16 * page.couchScale
                        }
                    }

                    ListView {
                        id: settingsList
                        objectName: "couchSettingsList"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        model: page.activeRows
                        spacing: 11 * page.couchScale
                        clip: true
                        interactive: contentHeight > height
                        boundsBehavior: Flickable.StopAtBounds

                        delegate: Rectangle {
                            id: settingDelegate
                            required property var modelData
                            required property int index
                            readonly property int globalIndex: page.globalIndexForId(modelData.id)
                            readonly property bool selected: page.selectedIndex === globalIndex
                            width: settingsList.width
                            height: 82 * page.couchScale
                            radius: 17 * page.couchScale
                            color: selected ? App.Theme.surfaceSelected : App.Theme.surfaceRaised
                            border.width: selected ? 4 * page.couchScale : 1
                            border.color: selected ? App.Theme.accent : App.Theme.border
                            opacity: modelData.enabled ? 1 : 0.58
                            scale: selected ? 1.015 : 1.0
                            transformOrigin: Item.Center

                            Behavior on scale {
                                NumberAnimation { duration: 150; easing.type: Easing.OutCubic }
                            }
                            Behavior on color {
                                ColorAnimation { duration: 140 }
                            }

                            Rectangle {
                                anchors.fill: parent
                                anchors.margins: 5 * page.couchScale
                                radius: 13 * page.couchScale
                                color: "transparent"
                                border.width: settingDelegate.selected ? 1 : 0
                                border.color: settingDelegate.selected ? App.Theme.accentGlow : "transparent"
                            }

                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 22 * page.couchScale
                                anchors.rightMargin: 18 * page.couchScale
                                spacing: 18 * page.couchScale

                                Label {
                                    Layout.fillWidth: true
                                    text: settingDelegate.modelData.title
                                    color: App.Theme.text
                                    font.pixelSize: 21 * page.couchScale
                                    font.weight: settingDelegate.selected ? Font.Bold : Font.DemiBold
                                    elide: Text.ElideRight
                                }

                                Rectangle {
                                    Layout.preferredWidth: Math.max(150 * page.couchScale,
                                                                    valueLabel.implicitWidth + 34 * page.couchScale)
                                    Layout.maximumWidth: 410 * page.couchScale
                                    Layout.preferredHeight: 46 * page.couchScale
                                    radius: height / 2
                                    color: settingDelegate.modelData.enabled
                                           ? (settingDelegate.selected ? App.Theme.accentSoft : App.Theme.surface)
                                           : App.Theme.backgroundElevated
                                    border.width: 1
                                    border.color: settingDelegate.selected ? App.Theme.accent : App.Theme.border

                                    Label {
                                        id: valueLabel
                                        anchors.centerIn: parent
                                        width: Math.min(implicitWidth, parent.width - 24 * page.couchScale)
                                        text: settingDelegate.modelData.value
                                        color: settingDelegate.modelData.enabled
                                               ? (settingDelegate.selected ? App.Theme.accent : App.Theme.textSecondary)
                                               : App.Theme.textMuted
                                        font.pixelSize: 18 * page.couchScale
                                        font.weight: Font.Bold
                                        horizontalAlignment: Text.AlignHCenter
                                        elide: Text.ElideRight
                                    }
                                }
                            }

                            MouseArea {
                                anchors.fill: parent
                                enabled: settingDelegate.modelData.enabled
                                onClicked: {
                                    page.selectIndex(settingDelegate.globalIndex, true)
                                    page.activate()
                                }
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 126 * page.couchScale
                        radius: 17 * page.couchScale
                        color: App.Theme.dark ? "#C7101722" : "#EAF3F7FB"
                        border.width: 1
                        border.color: page.selectedRow.enabled ? App.Theme.borderStrong : App.Theme.warning

                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 20 * page.couchScale
                            spacing: 16 * page.couchScale

                            Rectangle {
                                Layout.preferredWidth: 46 * page.couchScale
                                Layout.preferredHeight: 46 * page.couchScale
                                radius: width / 2
                                color: page.selectedRow.enabled ? App.Theme.accentSoft : App.Theme.warningSoft
                                Label {
                                    anchors.centerIn: parent
                                    text: page.selectedRow.enabled ? "i" : "!"
                                    color: page.selectedRow.enabled ? App.Theme.accent : App.Theme.warning
                                    font.pixelSize: 23 * page.couchScale
                                    font.weight: Font.Bold
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 4 * page.couchScale
                                Label {
                                    Layout.fillWidth: true
                                    text: page.selectedRow.title || ""
                                    color: App.Theme.text
                                    font.pixelSize: 19 * page.couchScale
                                    font.weight: Font.Bold
                                    elide: Text.ElideRight
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: page.selectedRow.description || ""
                                    color: App.Theme.textSecondary
                                    font.pixelSize: 16 * page.couchScale
                                    wrapMode: Text.WordWrap
                                    maximumLineCount: 2
                                    elide: Text.ElideRight
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    CouchOnScreenKeyboard {
        id: onScreenKeyboard
        anchors.fill: parent
        couchScale: page.couchScale
        buttonHints: page.controller && page.controller.gamepadButtonHints
                     ? page.controller.gamepadButtonHints : ({})
        onSemanticSound: function(kind) {
            if (page.controller && page.controller.playCouchSound)
                page.controller.playCouchSound(kind)
        }
        onAccepted: function(value) {
            var settingId = page.editingSettingId
            var saved = false
            var trimmed = String(value || "").trim()
            if (page.controller) {
                if (settingId === "steam-paths")
                    saved = page.controller.saveSetting("steamInstallationDirectories", page.parsePathList(value))
                else if (settingId === "game-paths")
                    saved = page.controller.saveSetting("libraryDirectories", page.parsePathList(value))
                else if (settingId === "auto-libraries")
                    saved = page.controller.saveSetting("automaticCompressionLibraries", page.parsePathList(value))
                else if (settingId === "auto-skipped") {
                    var appIds = page.parseAppIdList(value)
                    saved = appIds !== null
                            && page.controller.saveSetting("automaticCompressionSkippedAppIds", appIds)
                } else if (settingId === "backup-path")
                    saved = page.controller.saveSetting("backupDirectory", trimmed)
                else if (settingId === "quarantine-path")
                    saved = page.controller.saveSetting("quarantineDirectory", trimmed)
            }
            if (!saved) {
                page.playSemanticSound("error")
                var heading = onScreenKeyboard.title
                Qt.callLater(function() { onScreenKeyboard.open(value, heading) })
                return
            }
            page.playSemanticSound("confirm")
            page.editingSettingId = ""
            if (page.navigation)
                page.navigation.closeModal()
            Qt.callLater(page.restoreActiveFocus)
        }
        onCancelled: {
            page.editingSettingId = ""
            if (page.navigation)
                page.navigation.closeModal()
            Qt.callLater(page.restoreActiveFocus)
        }
    }
}
