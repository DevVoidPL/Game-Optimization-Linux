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
    readonly property real fitScale: height > 0 ? Math.min(couchScale, height / 1080) : couchScale
    readonly property real edgeMargin: 48 * fitScale
    readonly property real bottomNavMargin: 40 * fitScale
    readonly property real hintsBottomMargin: bottomNavMargin + bottomNav.height + App.Theme.couchSpaceS * fitScale
    readonly property real contentMargin: 96 * fitScale
    // 0 categories, 1 settings of the active category, 2 bottom navigation.
    property int focusZone: 1
    property int selectedTile: 3
    // Destructive actions (reset, component removal) need a confirmation
    // whose default choice (0) is the safe "Cancel".
    property string confirmRowId: ""
    property int confirmChoice: 0
    readonly property bool confirmationOpen: confirmRowId.length > 0
    readonly property var navTiles: [
        { "id": "library", "icon": App.UiIcons.couchGlyphLibrary, "iconOnLight": App.UiIcons.couchGlyphLibraryOnLight, "title": qsTr("Library"), "subtitle": qsTr("Your games") },
        { "id": "tasks", "icon": App.UiIcons.couchGlyphTasks, "iconOnLight": App.UiIcons.couchGlyphTasksOnLight, "title": qsTr("Tasks"), "subtitle": qsTr("Active and recent tasks") },
        { "id": "updates", "icon": App.UiIcons.couchGlyphUpdates, "iconOnLight": App.UiIcons.couchGlyphUpdatesOnLight, "title": qsTr("Updates"), "subtitle": qsTr("File changes and re-analysis") },
        { "id": "settings", "icon": App.UiIcons.couchGlyphSettings, "iconOnLight": App.UiIcons.couchGlyphSettingsOnLight, "title": qsTr("Settings"), "subtitle": qsTr("App configuration") }
    ]
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
        { "id": "general", "title": qsTr("General"), "subtitle": qsTr("Language, appearance and startup"), "icon": App.UiIcons.couchGlyphSettings, "iconOnLight": App.UiIcons.couchGlyphSettingsOnLight },
        { "id": "libraries", "title": qsTr("Libraries"), "subtitle": qsTr("Game locations and storage"), "icon": App.UiIcons.couchGlyphLibrary, "iconOnLight": App.UiIcons.couchGlyphLibraryOnLight },
        { "id": "automation", "title": qsTr("Automation"), "subtitle": qsTr("Updates and compression"), "icon": App.UiIcons.couchGlyphUpdates, "iconOnLight": App.UiIcons.couchGlyphUpdatesOnLight },
        { "id": "controller", "title": qsTr("Controller"), "subtitle": qsTr("Input and Couch behaviour"), "icon": App.UiIcons.couchGlyphController, "iconOnLight": App.UiIcons.couchGlyphControllerOnLight },
        { "id": "audio", "title": qsTr("Audio"), "subtitle": qsTr("Menu sounds and music"), "icon": App.UiIcons.couchGlyphVolume, "iconOnLight": App.UiIcons.couchGlyphVolumeOnLight },
        { "id": "narrator", "title": qsTr("Narrator"), "subtitle": qsTr("Local speech components"), "icon": App.UiIcons.couchGlyphNarrator, "iconOnLight": App.UiIcons.couchGlyphNarratorOnLight },
        { "id": "advanced", "title": qsTr("Advanced"), "subtitle": qsTr("Resources, diagnostics and mode"), "icon": App.UiIcons.couchGlyphTune, "iconOnLight": App.UiIcons.couchGlyphTuneOnLight }
    ]
    readonly property var rows: [
        { "id": "language", "category": "general", "title": qsTr("Language"), "value": languageLabel(setting("language", "English")), "enabled": true, "description": qsTr("Choose the interface language.") },
        { "id": "appearance", "category": "general", "title": qsTr("Appearance"), "value": themeLabel(setting("themeMode", "system")), "enabled": true, "description": qsTr("Follow the system colours or force a light or dark interface.") },
        { "id": "couch-motion", "category": "general", "title": qsTr("Couch Mode animations"), "value": motionLabel(setting("couchMotionMode", "full")), "enabled": true, "description": qsTr("Full, reduced or no movement in Couch Mode. Saved and applied immediately.") },
        { "id": "automatic-updates", "category": "automation", "title": qsTr("Automatic update checks"), "value": boolLabel(setting("automaticUpdates", true)), "enabled": true, "description": qsTr("Check for new releases without installing automatically.") },
        { "id": "log-level", "category": "advanced", "title": qsTr("Logging level"), "value": String(setting("logLevel", "INFO")), "enabled": true, "description": qsTr("Choose how much diagnostic information is recorded.") },
        { "id": "default-profile", "category": "automation", "title": qsTr("Default compression profile"), "value": enumLabel(setting("defaultCompressionProfile", "Auto")), "enabled": true, "description": qsTr("Preselected mode for storage operations.") },

        { "id": "auto-compression", "category": "automation", "title": qsTr("Automatic compression"), "value": enumLabel(setting("automaticCompressionMode", "Off")), "enabled": true, "description": qsTr("Choose which launcher events may trigger the guarded workflow.") },
        { "id": "auto-profile", "category": "automation", "title": qsTr("Automatic profile"), "value": enumLabel(setting("automaticCompressionProfile", "Auto")), "enabled": String(setting("automaticCompressionMode", "Off")) !== "Off", "description": qsTr("Auto compares measured levels; fixed profiles remain predictable.") },
        { "id": "auto-delay", "category": "automation", "title": qsTr("Safety delay"), "value": qsTr("%1 s").arg(Number(setting("automaticCompressionDelaySeconds", 300))), "enabled": String(setting("automaticCompressionMode", "Off")) !== "Off", "description": qsTr("Wait after the launcher becomes stable.") },
        { "id": "auto-jobs", "category": "automation", "title": qsTr("Maximum parallel jobs"), "value": String(Number(setting("automaticCompressionMaxJobs", 1))), "enabled": String(setting("automaticCompressionMode", "Off")) !== "Off", "description": qsTr("Limit concurrent automatic compression work from one to eight jobs.") },
        { "id": "auto-free", "category": "automation", "title": qsTr("Minimum free space"), "value": qsTr("%1 GiB").arg(Number(setting("automaticCompressionMinFreeGb", 10)).toFixed(1)), "enabled": String(setting("automaticCompressionMode", "Off")) !== "Off", "description": qsTr("Block automatic work below this free-space limit.") },
        { "id": "auto-notify", "category": "automation", "title": qsTr("Completion notifications"), "value": boolLabel(setting("automaticCompressionNotify", true)), "enabled": String(setting("automaticCompressionMode", "Off")) !== "Off", "description": qsTr("Notify when automatic work finishes or is blocked.") },
        { "id": "auto-libraries", "category": "automation", "title": qsTr("Automatic compression libraries"), "value": listLabel("automaticCompressionLibraries"), "enabled": String(setting("automaticCompressionMode", "Off")) !== "Off", "description": qsTr("Restrict automatic work to these existing library paths; separate paths with semicolons.") },
        { "id": "auto-skipped", "category": "automation", "title": qsTr("Skipped Steam AppIDs"), "value": listLabel("automaticCompressionSkippedAppIds"), "enabled": String(setting("automaticCompressionMode", "Off")) !== "Off", "description": qsTr("Never process these Steam games automatically; separate AppIDs with spaces or semicolons.") },

        { "id": "interface", "category": "general", "title": qsTr("Interface mode"), "value": enumLabel(setting("controllerMode", "Automatic")), "enabled": true, "description": qsTr("Choose whether GameOpti starts in the desktop or television-friendly interface.") },
        { "id": "swap", "category": "controller", "title": qsTr("Swap Confirm and Back"), "value": boolLabel(setting("swapAcceptBack", false)), "enabled": true, "description": qsTr("Reverse the two primary face-button actions.") },
        { "id": "deadzone", "category": "controller", "title": qsTr("Analog dead zone"), "value": qsTr("%1%").arg(Math.round(Number(setting("analogDeadzone", 0.20)) * 100)), "enabled": true, "description": qsTr("Ignore small stick movement around the center.") },
        { "id": "repeat-delay", "category": "controller", "title": qsTr("Navigation repeat delay"), "value": qsTr("%1 ms").arg(Number(setting("navigationRepeatDelayMs", 350))), "enabled": true, "description": qsTr("Delay before a held direction repeats.") },
        { "id": "repeat-rate", "category": "controller", "title": qsTr("Navigation repeat interval"), "value": qsTr("%1 ms").arg(Number(setting("navigationRepeatRateMs", 110))), "enabled": true, "description": qsTr("Time between repeated navigation steps.") },
        { "id": "cursor", "category": "controller", "title": qsTr("Hide cursor in Couch Mode"), "value": boolLabel(setting("hideCursorInCouchMode", true)), "enabled": true, "description": qsTr("The cursor returns after meaningful mouse movement.") },
        { "id": "fullscreen", "category": "controller", "title": qsTr("Start Couch Mode fullscreen"), "value": boolLabel(setting("startCouchModeFullscreen", true)), "enabled": true, "description": qsTr("Use the whole display when Couch Mode opens.") },
        { "id": "system-usage", "category": "controller", "title": qsTr("Show CPU/GPU/RAM usage"), "value": boolLabel(setting("couchShowSystemUsage", true)), "enabled": true, "description": qsTr("Show real processor, graphics and memory usage next to the clock. Values that cannot be read stay hidden.") },
        { "id": "post-launch", "category": "controller", "title": qsTr("After launching a game"), "value": enumLabel(setting("postLaunchBehavior", "Minimize")), "enabled": true, "description": qsTr("Choose what the GameOpti window should do.") },

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
    signal sectionRequested(string section)

    function restoreActiveFocus() {
        if (onScreenKeyboard.opened) {
            onScreenKeyboard.forceActiveFocus()
            onScreenKeyboard.focusSelected()
            return
        }
        forceActiveFocus()
        Qt.callLater(function() {
            if (!page.visible || onScreenKeyboard.opened)
                return
            if (page.confirmationOpen)
                (page.confirmChoice === 0 ? confirmCancelButton : confirmRunButton).forceActiveFocus()
            else if (page.focusZone === 0)
                categoryList.forceActiveFocus()
            else if (page.focusZone === 2) {
                var entry = bottomNav.itemAt(page.selectedTile)
                if (entry) entry.forceActiveFocus()
            } else
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

    // Display label for a stored enum value; the stored value is unchanged.
    function enumLabel(value) {
        switch (String(value)) {
        case "Off": return qsTr("Off")
        case "Automatic": return qsTr("Automatic")
        case "Desktop only": return qsTr("Desktop only")
        case "Couch only": return qsTr("Couch only")
        case "After new game installation": return qsTr("After new game installation")
        case "After game update": return qsTr("After game update")
        case "After installation and update": return qsTr("After installation and update")
        case "Minimize": return qsTr("Minimize")
        case "Stay open": return qsTr("Stay open")
        case "Close launcher": return qsTr("Close launcher")
        case "Fast": case "Balanced": case "Maximum": case "Auto": return App.I18n.profile(value)
        default: return String(value)
        }
    }

    function motionCode(value) {
        var normalized = String(value || "full")
        return normalized === "reduced" || normalized === "off" ? normalized : "full"
    }

    function motionLabel(value) {
        var code = motionCode(value)
        return code === "reduced" ? qsTr("Reduced") : code === "off" ? qsTr("Off") : qsTr("Full")
    }

    // Control type of a row, from its stable id.
    function rowKind(row) {
        var id = String(row && row.id || "")
        if (["automatic-updates", "auto-notify", "swap", "cursor", "fullscreen", "system-usage", "menu-sounds",
             "music", "steam-tools", "experimental", "narrator-global-enabled"].indexOf(id) >= 0)
            return "bool"
        if (["auto-libraries", "auto-skipped", "steam-paths", "game-paths",
             "backup-path", "quarantine-path"].indexOf(id) >= 0)
            return "path"
        if (["auto-delay", "auto-jobs", "auto-free", "deadzone", "repeat-delay", "repeat-rate",
             "menu-volume", "music-volume", "cpu-limit", "gpu-limit", "narrator-global-volume",
             "narrator-global-rate", "narrator-global-sampling", "narrator-global-change",
             "narrator-global-stabilization", "narrator-global-confidence", "narrator-global-cooldown",
             "narrator-global-noise-w", "narrator-global-noise"].indexOf(id) >= 0)
            return "number"
        if (id === "narrator-overview")
            return "info"
        if (id === "desktop" || id === "reset" || id === "forgotten-library" || row.componentAction)
            return "action"
        return "choice"
    }

    function destructive(row) {
        return row && (row.id === "reset" || row.componentAction === "remove")
    }

    function adjustable(row) {
        var kind = rowKind(row)
        return kind === "bool" || kind === "number" || kind === "choice" || row.id === "forgotten-library"
    }

    // Honest reason shown on a disabled row.
    function disabledReason(row) {
        if (!row || row.enabled)
            return ""
        var id = String(row.id || "")
        if (id.indexOf("auto-") === 0)
            return qsTr("Turn on automatic compression first")
        if (id === "menu-volume")
            return qsTr("Turn on menu sounds first")
        if (id === "music-volume")
            return qsTr("Turn on Couch Mode music first")
        if (id === "forgotten-library")
            return qsTr("No forgotten libraries")
        if (id.indexOf("narrator-global-") === 0)
            return narratorGlobalData.success === true ? qsTr("Not available with the current choice")
                                                       : qsTr("Narrator settings could not be loaded")
        return qsTr("Unavailable")
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
        // Actions run only on Confirm, never on left/right.
        if (row.componentAction)
            return false
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
        else if (id === "couch-motion")
            saveAdjusted("couchMotionMode", cycle(["full", "reduced", "off"], motionCode(setting("couchMotionMode", "full")), delta))
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
        else if (id === "system-usage")
            saveAdjusted("couchShowSystemUsage", setting("couchShowSystemUsage", true) !== true)
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
        if (destructive(row)) {
            openConfirmation(row.id)
            return true
        }
        return performRow(row)
    }

    function openConfirmation(rowId) {
        commitPendingVolumes()
        confirmRowId = String(rowId)
        confirmChoice = 0
        if (navigation)
            navigation.openModal("settings-confirm", "cancel")
        playSemanticSound("open")
        restoreActiveFocus()
    }

    function closeConfirmation(runAction) {
        if (!confirmationOpen)
            return false
        var rowIndex = globalIndexForId(confirmRowId)
        confirmRowId = ""
        confirmChoice = 0
        if (navigation)
            navigation.closeModal()
        var result = true
        if (runAction && rowIndex >= 0)
            result = performRow(rows[rowIndex])
        else
            playSemanticSound("back")
        restoreActiveFocus()
        return result
    }

    function performRow(row) {
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
            saved = Boolean(controller.saveSetting("couchShowSystemUsage", true)) && saved
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

    function activateTile() {
        var tile = navTiles[selectedTile]
        if (!tile)
            return false
        if (tile.id === "settings") {
            focusZone = 1
            return true
        }
        commitPendingVolumes()
        sectionRequested(tile.id)
        return true
    }

    function isLastActiveRow() {
        for (var index = activeRows.length - 1; index >= 0; --index) {
            if (activeRows[index].enabled)
                return selectedActiveIndex >= index
        }
        return true
    }

    function isFirstActiveRow() {
        for (var index = 0; index < activeRows.length; ++index) {
            if (activeRows[index].enabled)
                return selectedActiveIndex <= index
        }
        return true
    }

    function handleAction(action) {
        if (action === "Accept") action = "Confirm"
        else if (action === "PageLeft" || action === "PreviousSection") action = "PreviousTab"
        else if (action === "PageRight" || action === "NextSection") action = "NextTab"
        if (onScreenKeyboard.opened) {
            onScreenKeyboard.handleAction(action)
            return
        }
        if (confirmationOpen) {
            if (action === "Back")
                closeConfirmation(false)
            else if (["NavigateLeft", "NavigateRight", "NavigateUp", "NavigateDown"].indexOf(action) >= 0) {
                var choice = (action === "NavigateLeft" || action === "NavigateUp") ? 0 : 1
                if (choice !== confirmChoice) {
                    confirmChoice = choice
                    playSemanticSound("navigate")
                    restoreActiveFocus()
                }
            } else if (action === "Confirm")
                closeConfirmation(confirmChoice === 1)
            return
        }
        var previousZone = focusZone
        if (action === "PreviousTab") {
            changeCategory(-1)
        } else if (action === "NextTab") {
            changeCategory(1)
        } else if (focusZone === 0) {
            if (action === "Back") {
                commitPendingVolumes()
                backRequested()
                playSemanticSound("back")
            } else if (action === "NavigateUp") {
                changeCategory(-1)
            } else if (action === "NavigateDown") {
                if (activeCategoryIndex >= categories.length - 1)
                    focusZone = 2
                else
                    changeCategory(1)
            } else if (action === "NavigateRight" || action === "Confirm") {
                focusZone = 1
            }
        } else if (focusZone === 1) {
            var row = rows[selectedIndex] || ({})
            if (action === "Back") {
                commitPendingVolumes()
                focusZone = 0
            } else if (action === "NavigateUp") {
                if (!isFirstActiveRow() && move(-1)) playSemanticSound("navigate")
            } else if (action === "NavigateDown") {
                if (isLastActiveRow())
                    focusZone = 2
                else if (move(1))
                    playSemanticSound("navigate")
            } else if (action === "NavigateLeft") {
                if (adjustable(row))
                    change(-1)
                else
                    focusZone = 0
            } else if (action === "NavigateRight") {
                if (adjustable(row))
                    change(1)
            } else if (action === "Confirm") {
                activate()
            } else if (action === "PageUp") {
                if (move(-5)) playSemanticSound("navigate")
            } else if (action === "PageDown") {
                if (move(5)) playSemanticSound("navigate")
            }
        } else {
            if (action === "Back") {
                commitPendingVolumes()
                backRequested()
                playSemanticSound("back")
            } else if (action === "NavigateLeft" || action === "NavigateRight") {
                var tile = Math.max(0, Math.min(navTiles.length - 1,
                                                selectedTile + (action === "NavigateLeft" ? -1 : 1)))
                if (tile !== selectedTile) {
                    selectedTile = tile
                    playSemanticSound("navigate")
                }
            } else if (action === "NavigateUp") {
                // Back to the last used setting.
                focusZone = 1
            } else if (action === "Confirm") {
                playSemanticSound(activateTile() ? "confirm" : "error")
            }
        }
        if (focusZone !== previousZone) {
            playSemanticSound("navigate")
            restoreActiveFocus()
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
            selectedTile = 3
            focusZone = 1
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

    CouchHeroBackdrop {
        anchors.fill: parent
        sources: []
    }

    // ---- Header --------------------------------------------------------------
    ColumnLayout {
        id: header
        objectName: "couchSettingsHeader"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.leftMargin: page.contentMargin
        anchors.rightMargin: page.contentMargin
        anchors.topMargin: 92 * page.fitScale
        spacing: 4 * page.fitScale
        Label {
            objectName: "couchSettingsTitle"
            text: qsTr("Settings")
            color: App.Theme.text
            font.pixelSize: App.Theme.couchTitleSize * page.fitScale
            font.weight: Font.Bold
        }
        Label {
            Layout.fillWidth: true
            text: qsTr("Choose a category, then change its options with the controller. Changes are saved locally right away.")
            color: App.Theme.textSecondary
            font.pixelSize: 18 * page.fitScale
            elide: Text.ElideRight
        }
    }

    // ---- Categories (focus zone 0) ---------------------------------------------
    ListView {
        id: categoryList
        objectName: "couchSettingsCategories"
        // Room for the focused cell's scale, ring and glow inside the clip.
        readonly property real pad: 30 * page.fitScale
        anchors.left: parent.left
        anchors.top: header.bottom
        anchors.bottom: footerRow.top
        anchors.leftMargin: page.contentMargin - pad
        anchors.topMargin: App.Theme.couchSpaceL * page.fitScale
        width: 380 * page.fitScale + 2 * pad
        model: page.categories
        currentIndex: page.activeCategoryIndex
        spacing: App.Theme.couchSpaceS * page.fitScale
        clip: true
        topMargin: pad
        bottomMargin: pad
        keyNavigationEnabled: false
        boundsBehavior: Flickable.StopAtBounds
        highlightMoveDuration: App.Theme.couchMotionDuration(160)
        highlightMoveVelocity: -1
        preferredHighlightBegin: pad
        preferredHighlightEnd: height - pad
        highlightRangeMode: ListView.ApplyRange

        delegate: Item {
            id: categoryDelegate
            objectName: "couchSettingsCategory"
            required property var modelData
            required property int index
            readonly property bool active: page.activeCategoryIndex === index
            readonly property bool focused: active && page.focusZone === 0
                                            && !page.keyboardOpen && !page.confirmationOpen
            // Delegate spans the view; the visual card is inset by `pad` so the
            // ring and glow stay inside the clip. No x binding on the delegate.
            width: categoryList.width
            height: 78 * page.fitScale
            z: focused ? 2 : 1

            Rectangle {
                id: categoryCard
                objectName: "couchSettingsCategoryCard"
                anchors.fill: parent
                anchors.leftMargin: categoryList.pad
                anchors.rightMargin: categoryList.pad
                radius: App.Theme.couchCardRadius * page.fitScale
                color: categoryDelegate.focused ? App.Theme.couchFocusSurface
                       : categoryDelegate.active ? App.Theme.accentSoft
                       : Qt.rgba(App.Theme.surface.r, App.Theme.surface.g, App.Theme.surface.b,
                                 App.Theme.dark ? 0.72 : 0.86)
                border.width: categoryDelegate.active && !categoryDelegate.focused ? 2 * page.fitScale : 1
                border.color: categoryDelegate.active ? App.Theme.accent : App.Theme.border
                // Focus is shown by the ring only: no scale in lists.
                Behavior on color { ColorAnimation { duration: App.Theme.couchFadeDuration(150) } }
                CouchFocusFrame {
                    active: categoryDelegate.focused
                    radius: parent.radius
                    couchScale: page.fitScale
                }
            }
            RowLayout {
                anchors.fill: categoryCard
                anchors.leftMargin: App.Theme.couchSpaceM * page.fitScale
                anchors.rightMargin: App.Theme.couchSpaceM * page.fitScale
                spacing: App.Theme.couchSpaceM * page.fitScale
                Rectangle {
                    Layout.preferredWidth: 48 * page.fitScale
                    Layout.preferredHeight: 48 * page.fitScale
                    radius: App.Theme.couchRadiusSmall * page.fitScale
                    color: App.Theme.surfaceRaised
                    CouchIcon {
                        anchors.centerIn: parent
                        size: App.Theme.couchIconMedium
                        couchScale: page.fitScale
                        source: categoryDelegate.modelData.icon
                        lightSource: categoryDelegate.modelData.iconOnLight
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 1
                    Label {
                        Layout.fillWidth: true
                        text: categoryDelegate.modelData.title
                        color: categoryDelegate.focused ? App.Theme.couchFocusText : App.Theme.text
                        font.pixelSize: 21 * page.fitScale
                        font.weight: Font.Bold
                        elide: Text.ElideRight
                    }
                    Label {
                        Layout.fillWidth: true
                        text: categoryDelegate.modelData.subtitle
                        color: App.Theme.textSecondary
                        font.pixelSize: App.Theme.couchCaptionSize * page.fitScale
                        elide: Text.ElideRight
                    }
                }
            }
            MouseArea {
                anchors.fill: parent
                onClicked: {
                    page.focusZone = 0
                    page.selectCategory(categoryDelegate.index)
                    page.restoreActiveFocus()
                }
            }
        }
    }

    // ---- Settings of the active category (focus zone 1) -------------------------
    ColumnLayout {
        id: contentColumn
        anchors.left: categoryList.right
        anchors.right: parent.right
        anchors.top: header.bottom
        anchors.bottom: footerRow.top
        // The list clips, so it keeps room for the ring and glow of the focused
        // row (settingsList.pad); the margins subtract it so rows stay put.
        anchors.leftMargin: App.Theme.couchSpaceXL * page.fitScale - categoryList.pad
                            + 12 * page.fitScale - settingsList.pad
        anchors.rightMargin: page.contentMargin - settingsList.pad
        anchors.topMargin: App.Theme.couchSpaceL * page.fitScale
        spacing: App.Theme.couchSpaceS * page.fitScale

        ColumnLayout {
            Layout.fillWidth: true
            Layout.leftMargin: settingsList.pad
            spacing: 2 * page.fitScale
            Label {
                objectName: "couchSettingsCategoryTitle"
                Layout.fillWidth: true
                text: page.categories[page.activeCategoryIndex]
                      ? page.categories[page.activeCategoryIndex].title : ""
                color: App.Theme.text
                font.pixelSize: 28 * page.fitScale
                font.weight: Font.Bold
            }
            Label {
                Layout.fillWidth: true
                text: page.categories[page.activeCategoryIndex]
                      && page.categories[page.activeCategoryIndex].id === "narrator"
                      ? qsTr("Global defaults for new per-game profiles. A game's own Narrator profile can override them.")
                      : qsTr("Left and right change the selected value. A opens editors and actions.")
                color: App.Theme.textSecondary
                font.pixelSize: App.Theme.couchLabelSize * page.fitScale
                elide: Text.ElideRight
            }
        }

        ListView {
            id: settingsList
            objectName: "couchSettingsList"
            // Ring (4) + glow blur (22) + a little air, all fully inside the clip.
            readonly property real pad: 30 * page.fitScale
            Layout.fillWidth: true
            Layout.fillHeight: true
            model: page.activeRows
            currentIndex: page.selectedActiveIndex
            spacing: App.Theme.couchSpaceS * page.fitScale
            clip: true
            topMargin: pad
            bottomMargin: pad
            keyNavigationEnabled: false
            boundsBehavior: Flickable.StopAtBounds
            highlightMoveDuration: App.Theme.couchMotionDuration(160)
            highlightMoveVelocity: -1
            preferredHighlightBegin: pad
            preferredHighlightEnd: height - pad
            highlightRangeMode: ListView.ApplyRange

            delegate: Item {
                id: settingDelegate
                objectName: "couchSettingRow"
                required property var modelData
                required property int index
                readonly property int globalIndex: page.globalIndexForId(modelData.id)
                readonly property string kind: page.rowKind(modelData)
                readonly property bool selected: page.selectedIndex === globalIndex
                readonly property bool focused: selected && page.focusZone === 1
                                                && !page.keyboardOpen && !page.confirmationOpen
                readonly property string reason: page.disabledReason(modelData)
                width: settingsList.width
                height: 92 * page.fitScale
                z: focused ? 2 : 1

                Rectangle {
                    id: rowCard
                    objectName: "couchSettingRowCard"
                    anchors.fill: parent
                    anchors.leftMargin: settingsList.pad
                    anchors.rightMargin: settingsList.pad
                    radius: App.Theme.couchCardRadius * page.fitScale
                    color: settingDelegate.focused ? App.Theme.couchFocusSurface
                           : Qt.rgba(App.Theme.surface.r, App.Theme.surface.g, App.Theme.surface.b,
                                     App.Theme.dark ? 0.86 : 0.94)
                    border.width: 1
                    border.color: settingDelegate.selected && !settingDelegate.focused
                                  ? App.Theme.borderStrong : App.Theme.border
                    // Focus is shown by the ring only: no scale in lists.
                    Behavior on color { ColorAnimation { duration: App.Theme.couchFadeDuration(150) } }
                    CouchFocusFrame {
                        active: settingDelegate.focused
                        radius: parent.radius
                        couchScale: page.fitScale
                    }
                }

                // The category icon lives in the category column only.
                RowLayout {
                    anchors.fill: rowCard
                    anchors.leftMargin: App.Theme.couchSpaceL * page.fitScale
                    anchors.rightMargin: App.Theme.couchSpaceL * page.fitScale
                    spacing: App.Theme.couchSpaceM * page.fitScale
                    opacity: settingDelegate.modelData.enabled ? 1 : 0.62

                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        spacing: 3 * page.fitScale
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: App.Theme.couchSpaceS * page.fitScale
                            // Takes all free width; elides only when it really
                            // does not fit (no maximumWidth: implicitWidth, whose
                            // integer rounding made every title elide).
                            Label {
                                objectName: "couchSettingTitle"
                                Layout.fillWidth: true
                                text: settingDelegate.modelData.title
                                color: settingDelegate.focused ? App.Theme.couchFocusText : App.Theme.text
                                font.pixelSize: 21 * page.fitScale
                                font.weight: Font.Bold
                                elide: Text.ElideRight
                            }
                            CouchStatePill {
                                objectName: "couchSettingExperimental"
                                visible: settingDelegate.modelData.id === "experimental"
                                couchScale: page.fitScale * 0.85
                                tone: "warning"
                                text: qsTr("Experimental")
                            }
                        }
                        Label {
                            Layout.fillWidth: true
                            text: settingDelegate.reason.length ? settingDelegate.reason
                                                                : String(settingDelegate.modelData.description || "")
                            color: settingDelegate.focused ? App.Theme.couchFocusSubtext : App.Theme.textSecondary
                            font.pixelSize: 15 * page.fitScale
                            elide: Text.ElideRight
                        }
                    }

                    // Control matching the real data type.
                    Row {
                        objectName: "couchSettingControl"
                        property string kind: settingDelegate.kind
                        Layout.alignment: Qt.AlignVCenter
                        Layout.maximumWidth: 460 * page.fitScale
                        spacing: App.Theme.couchSpaceS * page.fitScale
                        CouchIcon {
                            anchors.verticalCenter: parent.verticalCenter
                            visible: settingDelegate.modelData.enabled
                                     && (settingDelegate.kind === "choice" || settingDelegate.kind === "number"
                                         || settingDelegate.modelData.id === "forgotten-library")
                            size: App.Theme.couchIconSmall
                            couchScale: page.fitScale
                            source: App.UiIcons.couchGlyphPrevious
                            lightSource: App.UiIcons.couchGlyphPreviousOnLight
                        }
                        CouchIcon {
                            anchors.verticalCenter: parent.verticalCenter
                            visible: settingDelegate.kind === "bool"
                            size: App.Theme.couchIconMedium
                            couchScale: page.fitScale
                            muted: !settingDelegate.modelData.enabled
                            source: settingDelegate.modelData.value === page.boolLabel(true)
                                    ? App.UiIcons.couchGlyphToggleOn : App.UiIcons.couchGlyphToggleOff
                            lightSource: settingDelegate.modelData.value === page.boolLabel(true)
                                         ? App.UiIcons.couchGlyphToggleOnOnLight : App.UiIcons.couchGlyphToggleOffOnLight
                        }
                        Label {
                            objectName: "couchSettingValue"
                            anchors.verticalCenter: parent.verticalCenter
                            width: Math.min(implicitWidth, 340 * page.fitScale)
                            text: settingDelegate.modelData.enabled || settingDelegate.kind !== "action"
                                  ? String(settingDelegate.modelData.value) : qsTr("Unavailable")
                            color: settingDelegate.focused ? App.Theme.couchFocusText
                                   : page.destructive(settingDelegate.modelData) ? App.Theme.couchToneText("danger")
                                   : App.Theme.text
                            font.pixelSize: 19 * page.fitScale
                            font.weight: Font.DemiBold
                            horizontalAlignment: Text.AlignRight
                            elide: settingDelegate.kind === "path" ? Text.ElideMiddle : Text.ElideRight
                        }
                        CouchIcon {
                            anchors.verticalCenter: parent.verticalCenter
                            visible: settingDelegate.modelData.enabled
                                     && (settingDelegate.kind === "choice" || settingDelegate.kind === "number"
                                         || settingDelegate.modelData.id === "forgotten-library")
                            size: App.Theme.couchIconSmall
                            couchScale: page.fitScale
                            source: App.UiIcons.couchGlyphNext
                            lightSource: App.UiIcons.couchGlyphNextOnLight
                        }
                        CouchIcon {
                            anchors.verticalCenter: parent.verticalCenter
                            visible: settingDelegate.modelData.enabled
                                     && (settingDelegate.kind === "path"
                                         || (settingDelegate.kind === "action"
                                             && settingDelegate.modelData.id !== "forgotten-library"))
                            size: App.Theme.couchIconSmall
                            couchScale: page.fitScale
                            source: settingDelegate.kind === "path" ? App.UiIcons.couchGlyphKeyboard : App.UiIcons.couchGlyphNext
                            lightSource: settingDelegate.kind === "path" ? App.UiIcons.couchGlyphKeyboardOnLight
                                                                          : App.UiIcons.couchGlyphNextOnLight
                        }
                    }
                }

                MouseArea {
                    anchors.fill: parent
                    enabled: settingDelegate.modelData.enabled
                    onClicked: {
                        page.focusZone = 1
                        page.selectIndex(settingDelegate.globalIndex, true)
                        page.activate()
                        page.restoreActiveFocus()
                    }
                }
            }
        }
    }

    Item {
        id: footerRow
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: bottomNav.top
        anchors.bottomMargin: App.Theme.couchSpaceS * page.fitScale
        height: 52 * page.fitScale
    }

    // ---- Global section navigation (focus zone 2) ----------------------------------
    CouchBottomNav {
        id: bottomNav
        objectName: "couchSettingsNavigation"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: page.edgeMargin
        anchors.rightMargin: page.edgeMargin
        anchors.bottomMargin: page.bottomNavMargin
        height: implicitHeight
        couchScale: page.fitScale
        model: page.navTiles
        activeIndex: 3
        currentIndex: page.selectedTile
        navFocused: page.focusZone === 2 && !page.keyboardOpen && !page.confirmationOpen
        onActivated: function(index) {
            page.focusZone = 2
            page.selectedTile = index
            page.playSemanticSound(page.activateTile() ? "confirm" : "error")
        }
    }

    // ---- Confirmation for destructive actions (safe default: Cancel) ---------------
    CouchOverlayFrame {
        objectName: "couchSettingsConfirmation"
        anchors.fill: parent
        z: 120
        visible: page.confirmationOpen
        couchScale: page.couchScale
        maximumWidth: 760 * page.couchScale
        preferredHeight: 330 * page.couchScale

        ColumnLayout {
            anchors.fill: parent
            spacing: 18 * page.couchScale
            Label {
                Layout.fillWidth: true
                text: page.confirmRowId.length && page.rows[page.globalIndexForId(page.confirmRowId)]
                      ? page.rows[page.globalIndexForId(page.confirmRowId)].title : ""
                color: App.Theme.text
                font.pixelSize: 32 * page.couchScale
                font.weight: Font.Bold
                elide: Text.ElideRight
            }
            Label {
                Layout.fillWidth: true
                text: page.confirmRowId.length && page.rows[page.globalIndexForId(page.confirmRowId)]
                      ? page.rows[page.globalIndexForId(page.confirmRowId)].description : ""
                color: App.Theme.textSecondary
                font.pixelSize: 18 * page.couchScale
                wrapMode: Text.WordWrap
            }
            Item { Layout.fillHeight: true }
            RowLayout {
                Layout.fillWidth: true
                spacing: 16 * page.couchScale
                CouchButton {
                    id: confirmCancelButton
                    Layout.fillWidth: true
                    Layout.preferredHeight: 66 * page.couchScale
                    couchScale: page.couchScale
                    iconSource: App.UiIcons.couchGlyphPrevious
                    iconLightSource: App.UiIcons.couchGlyphPreviousOnLight
                    text: qsTr("Cancel")
                    focus: page.confirmationOpen && page.confirmChoice === 0
                    onClicked: page.closeConfirmation(false)
                }
                CouchButton {
                    id: confirmRunButton
                    Layout.fillWidth: true
                    Layout.preferredHeight: 66 * page.couchScale
                    couchScale: page.couchScale
                    iconSource: App.UiIcons.couchGlyphRemove
                    iconLightSource: App.UiIcons.couchGlyphRemoveOnLight
                    text: qsTr("Continue")
                    focus: page.confirmationOpen && page.confirmChoice === 1
                    onClicked: page.closeConfirmation(true)
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
        keyboardInput: !(page.controller && page.controller.activeController
                         && page.controller.activeController.name)
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
