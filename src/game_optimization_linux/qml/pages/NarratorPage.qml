pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../components"
import ".." as App

Item {
    id: page

    property var controller
    property var gamesData: controller && controller.games ? controller.games : []
    property var componentsData: controller && controller.narratorComponents
                                 ? controller.narratorComponents : []
    property string selectedGameId: ""
    property var sessionData: ({ "status": "idle" })

    property bool narratorEnabled: false
    property string sourceMode: "auto"
    property string captureSource: "window"
    property string subtitleLanguageMode: "english_to_polish"
    property string ocrProviderId: ""
    property string translationProviderId: ""
    property string translationProfile: ""
    property var translationProfiles: []
    property string ttsProviderId: ""
    property string voiceId: ""
    property var voices: []
    property real narratorVolume: 0.85
    property real speechRate: 1.0
    // Advanced articulation overrides. When articulationOverridden is false the
    // values are not sent at all, so each voice's own defaults apply. The
    // numbers below are only the slider starting positions and match the values
    // both installed Polish voices already use.
    property bool articulationOverridden: false
    property real noiseWScale: 0.8
    property real noiseScale: 0.667
    property real cropX: 0.05
    property real cropY: 0.62
    property real cropWidth: 0.90
    property real cropHeight: 0.30
    property bool advancedRegionVisible: false
    property bool regionPreviewLoading: false
    property string regionPreviewError: ""
    property url regionPreviewSource: ""
    property int regionPreviewSourceWidth: 0
    property int regionPreviewSourceHeight: 0
    property bool recentOcrDecisionsExpanded: false
    readonly property var gameRows: buildGameRows()
    readonly property string sessionStatus: String(value(
                                                        sessionData,
                                                        ["status"],
                                                        "idle"))
    readonly property bool sessionActive: [
        "starting", "selecting_source", "listening", "ocr",
        "translating", "speaking", "stopping"
    ].indexOf(sessionStatus) >= 0
    readonly property bool componentsReady: requiredComponentsReady()
    readonly property bool selectedVoiceReady: selectedVoiceAvailable()

    signal toastRequested(string message, string tone)

    function value(source, keys, fallback) {
        var values = source || {}
        for (var i = 0; i < keys.length; ++i) {
            var candidate = values[keys[i]]
            if (candidate !== undefined && candidate !== null && candidate !== "")
                return candidate
        }
        return fallback
    }

    function buildGameRows() {
        var rows = []
        var source = gamesData || []
        for (var i = 0; i < source.length; ++i) {
            var game = source[i] || {}
            var gameId = String(value(game, ["gameId", "game_id", "id"], ""))
            if (!gameId.length)
                continue
            rows.push({
                "id": gameId,
                "label": String(value(game, ["name", "title"], gameId))
            })
        }
        rows.sort(function(left, right) {
            return left.label.localeCompare(right.label)
        })
        return rows
    }

    function gameLabels() {
        var labels = []
        for (var i = 0; i < gameRows.length; ++i)
            labels.push(gameRows[i].label)
        return labels
    }

    function selectedGameIndex() {
        for (var i = 0; i < gameRows.length; ++i) {
            if (gameRows[i].id === selectedGameId)
                return i
        }
        return gameRows.length ? 0 : -1
    }

    function indexOfValue(values, wanted, fallback) {
        for (var i = 0; i < values.length; ++i) {
            if (String(values[i]) === String(wanted))
                return i
        }
        return fallback
    }

    function ensureSelection() {
        if (!gameRows.length) {
            selectedGameId = ""
            sessionData = ({ "status": "idle" })
            return
        }
        var index = selectedGameIndex()
        selectGame(gameRows[Math.max(0, index)].id)
    }

    function selectGame(gameId) {
        var normalized = String(gameId || "")
        if (!normalized.length)
            return
        if (selectedGameId.length && normalized !== selectedGameId)
            cancelRegionPreview()
        if (normalized !== selectedGameId) {
            regionPreviewSource = ""
            regionPreviewSourceWidth = 0
            regionPreviewSourceHeight = 0
            regionPreviewError = ""
        }
        selectedGameId = normalized
        loadSettings()
        refreshSession()
    }

    function loadSettings() {
        if (!selectedGameId.length || !controller
                || typeof controller.getNarratorGameSettings !== "function")
            return
        var settings = controller.getNarratorGameSettings(selectedGameId) || {}
        narratorEnabled = Boolean(value(settings, ["enabled"], false))
        sourceMode = String(value(settings, ["source_mode", "sourceMode"], "auto"))
        captureSource = String(value(settings, ["capture_source", "captureSource"], "window"))
        subtitleLanguageMode = String(value(
                                          settings,
                                          ["subtitle_language_mode", "subtitleLanguageMode"],
                                          "english_to_polish"))
        ocrProviderId = String(value(settings, ["ocr_provider_id", "ocrProviderId"], ""))
        translationProviderId = String(value(settings, ["translation_provider_id", "translationProviderId"], ""))
        translationProfile = String(value(
                                        settings,
                                        ["translation_profile_id", "translationProfileId"],
                                        ""))
        translationProfiles = value(settings, ["translationProfiles"], []) || []
        ttsProviderId = String(value(settings, ["tts_provider_id", "ttsProviderId"], ""))
        voiceId = String(value(settings, ["voice_id", "voiceId"], ""))
        voices = value(settings, ["voices"], []) || []
        narratorVolume = Number(value(settings, ["volume"], 0.85))
        speechRate = Number(value(settings, ["speech_rate", "speechRate"], 1.0))
        // A null/absent value means the voice's own articulation is in use.
        var storedNoiseW = value(settings, ["noise_w_scale", "noiseWScale"], null)
        var storedNoise = value(settings, ["noise_scale", "noiseScale"], null)
        articulationOverridden = storedNoiseW !== null || storedNoise !== null
        if (storedNoiseW !== null)
            noiseWScale = Number(storedNoiseW)
        if (storedNoise !== null)
            noiseScale = Number(storedNoise)
        var region = value(settings, ["subtitle_region", "subtitleRegion"], ({})) || {}
        cropX = Number(value(region, ["x"], 0.05))
        cropY = Number(value(region, ["y"], 0.62))
        cropWidth = Number(value(region, ["width"], 0.90))
        cropHeight = Number(value(region, ["height"], 0.30))
    }

    function settingsPayload() {
        return {
            "enabled": narratorEnabled,
            "sourceMode": sourceMode,
            "captureSource": captureSource,
            "subtitleLanguageMode": subtitleLanguageMode,
            "ocrProviderId": ocrProviderId,
            "translationProviderId": translationProviderId,
            "translationProfileId": translationProfile,
            "ttsProviderId": ttsProviderId,
            "voiceId": voiceId,
            "volume": narratorVolume,
            "speechRate": speechRate,
            // null tells the backend to leave articulation to the voice.
            "noiseWScale": articulationOverridden ? noiseWScale : null,
            "noiseScale": articulationOverridden ? noiseScale : null,
            "subtitleRegion": {
                "x": cropX,
                "y": cropY,
                "width": cropWidth,
                "height": cropHeight
            }
        }
    }

    function saveSettings() {
        if (!selectedGameId.length || !controller
                || typeof controller.saveNarratorGameSettings !== "function")
            return false
        var saved = Boolean(controller.saveNarratorGameSettings(
                                selectedGameId, settingsPayload()))
        return saved
    }

    function refreshSession() {
        if (!selectedGameId.length || !controller
                || typeof controller.getNarratorSessionState !== "function") {
            sessionData = ({ "status": "idle" })
            return
        }
        sessionData = controller.getNarratorSessionState(selectedGameId)
                      || ({ "status": "idle" })
    }

    function startNarrator() {
        if (!saveSettings() || !controller
                || typeof controller.startNarrator !== "function")
            return
        controller.startNarrator(selectedGameId)
        refreshSession()
    }

    function stopNarrator() {
        if (controller && typeof controller.stopNarrator === "function")
            controller.stopNarrator()
        refreshSession()
    }

    function resetSubtitleRegion() {
        cropX = 0.05
        cropY = 0.62
        cropWidth = 0.90
        cropHeight = 0.30
    }

    function requestRegionPreview() {
        regionPreviewError = ""
        if (!selectedGameId.length || !controller
                || typeof controller.selectNarratorSubtitleRegion !== "function") {
            regionPreviewError = qsTr("Screen capture is unavailable.")
            return
        }
        regionPreviewLoading = true
        if (!controller.selectNarratorSubtitleRegion(
                    selectedGameId,
                    {"x": cropX, "y": cropY,
                     "width": cropWidth, "height": cropHeight}))
            regionPreviewLoading = false
    }

    function cancelRegionPreview() {
        if (controller && typeof controller.cancelNarratorRegionPreview === "function")
            controller.cancelNarratorRegionPreview(selectedGameId)
        regionPreviewLoading = false
    }

    function applyRegionPreview(gameId, preview) {
        if (String(gameId || "") !== selectedGameId)
            return
        var values = preview || {}
        var state = String(value(values, ["state"], ""))
        if (state === "ready" && Boolean(value(values, ["success"], false))) {
            regionPreviewLoading = false
            regionPreviewError = ""
            regionPreviewSource = String(value(values, ["imageUrl"], ""))
            regionPreviewSourceWidth = Number(value(values, ["sourceWidth"], 0))
            regionPreviewSourceHeight = Number(value(values, ["sourceHeight"], 0))
            return
        }
        if (!Boolean(value(values, ["success"], true))) {
            regionPreviewLoading = false
            regionPreviewError = String(value(
                                            values,
                                            ["error", "message"],
                                            qsTr("The selected game frame could not be captured.")))
        }
    }

    function applyNativeRegionSelection(gameId, selection) {
        if (String(gameId || "") !== selectedGameId)
            return
        var region = selection || {}
        cropX = Number(value(region, ["x"], cropX))
        cropY = Number(value(region, ["y"], cropY))
        cropWidth = Number(value(region, ["width"], cropWidth))
        cropHeight = Number(value(region, ["height"], cropHeight))
        regionPreviewError = ""
        refreshSession()
    }

    function requiredComponentsReady() {
        var required = {}
        required["capture.portal-pipewire"] = false
        required[subtitleLanguageMode === "polish"
                 ? "ocr.polish-local" : "ocr.english-local"] = false
        required["audio.qt-pcm"] = false
        if (subtitleLanguageMode !== "polish")
            required["translation.opus-en-pl"] = false
        var source = componentsData || []
        for (var i = 0; i < source.length; ++i) {
            var componentId = String(value(source[i], ["componentId", "component_id"], ""))
            if (required[componentId] !== undefined
                    && String(value(source[i], ["state"], "")) === "available")
                required[componentId] = true
        }
        for (var key in required) {
            if (!required[key])
                return false
        }
        return true
    }

    function optionLabels(options) {
        var labels = []
        for (var i = 0; i < (options || []).length; ++i)
            labels.push(String(value(options[i], ["name", "id"], "")))
        return labels
    }

    function optionIds(options) {
        var ids = []
        for (var i = 0; i < (options || []).length; ++i)
            ids.push(String(value(options[i], ["id"], "")))
        return ids
    }

    function selectedVoice() {
        for (var i = 0; i < (voices || []).length; ++i) {
            if (String(value(voices[i], ["id"], "")) === voiceId)
                return voices[i]
        }
        return null
    }

    function selectedVoiceInstalled() {
        var voice = selectedVoice()
        return voice !== null && Boolean(value(voice, ["installed"], false))
    }

    function selectedVoiceAvailable() {
        var voice = selectedVoice()
        return voice !== null && Boolean(value(voice, ["available"], false))
    }

    function selectedVoiceComponent() {
        var voice = selectedVoice()
        if (voice === null)
            return null
        var componentId = String(value(voice, ["componentId", "component_id"], ""))
        for (var i = 0; i < (componentsData || []).length; ++i) {
            if (String(value(componentsData[i], ["componentId", "component_id"], ""))
                    === componentId)
                return componentsData[i]
        }
        return null
    }

    function installSelectedVoice() {
        var component = selectedVoiceComponent()
        if (component === null || !saveSettings())
            return
        runComponentAction(component)
    }

    function translationProfileLabels(options) {
        var labels = []
        for (var i = 0; i < (options || []).length; ++i) {
            var profileId = String(value(options[i], ["id"], ""))
            if (profileId === "balanced")
                labels.push(qsTr("Balanced"))
            else if (profileId === "fast")
                labels.push(qsTr("Fast"))
            else
                labels.push(String(value(options[i], ["name", "id"], "")))
        }
        return labels
    }

    function componentName(component) {
        var componentId = String(value(component, ["componentId", "component_id"], ""))
        if (componentId === "capture.portal-pipewire")
            return qsTr("Wayland portal and PipeWire capture")
        if (componentId === "ocr.english-local")
            return qsTr("Local English subtitle OCR")
        if (componentId === "ocr.polish-local")
            return qsTr("Local Polish subtitle OCR")
        if (componentId === "translation.opus-en-pl")
            return qsTr("Local English to Polish translation")
        if (componentId === "tts.polish-voice")
            return qsTr("Local Polish voice") + " - Gosia"
        if (componentId === "tts.polish-bass")
            return qsTr("Local Polish voice") + " - Bass"
        if (componentId === "audio.qt-pcm")
            return qsTr("PCM audio output")
        return String(value(component, ["name"], componentId))
    }

    function componentKind(kind) {
        if (kind === "capture")
            return qsTr("Screen capture")
        if (kind === "ocr")
            return qsTr("Text recognition")
        if (kind === "translation")
            return qsTr("Translation")
        if (kind === "tts")
            return qsTr("Polish speech")
        if (kind === "audio")
            return qsTr("Audio output")
        return qsTr("Component")
    }

    function componentDescription(component) {
        var code = String(value(component, ["descriptionCode", "description_code"], ""))
        if ((code === "ocr_model_required" && subtitleLanguageMode === "polish")
                || (code === "polish_ocr_model_required"
                    && subtitleLanguageMode !== "polish")
                || (code === "translation_model_required"
                    && subtitleLanguageMode === "polish"))
            return qsTr("Not required for the selected subtitle language.")
        if (code === "capture_runtime")
            return qsTr("Capture permission is requested through the system portal when a session starts.")
        if (code === "ocr_model_required")
            return qsTr("A verified local English OCR runtime and model are required.")
        if (code === "polish_ocr_model_required")
            return qsTr("A verified local Polish OCR runtime and model are required.")
        if (code === "translation_model_required")
            return qsTr("A verified local English to Polish translation model is required.")
        if (code === "polish_voice_required")
            return qsTr("A Polish voice model with verified licensing is required.")
        if (code === "audio_runtime")
            return qsTr("Generated PCM audio is played through the sandbox audio service.")
        return ""
    }

    function componentStateLabel(state) {
        if (state === "available")
            return qsTr("Available")
        if (state === "not_installed")
            return qsTr("Not installed")
        if (state === "installing")
            return qsTr("Installing")
        if (state === "update_available")
            return qsTr("Update available")
        if (state === "unsupported")
            return qsTr("Unavailable in this build")
        if (state === "error")
            return qsTr("Error")
        return qsTr("Unknown")
    }

    function componentTone(state) {
        if (state === "available")
            return "available"
        if (state === "update_available" || state === "installing")
            return "warning"
        if (state === "error")
            return "failed"
        return "not checked"
    }

    function sessionLabel(state) {
        if (state === "idle")
            return qsTr("Stopped")
        if (state === "starting")
            return qsTr("Starting")
        if (state === "selecting_source")
            return qsTr("Select a screen or window")
        if (state === "listening")
            return qsTr("Waiting for subtitles")
        if (state === "ocr")
            return qsTr("Recognizing text")
        if (state === "translating")
            return qsTr("Translating")
        if (state === "speaking")
            return qsTr("Speaking")
        if (state === "stopping")
            return qsTr("Stopping")
        if (state === "stopped")
            return qsTr("Stopped")
        if (state === "error")
            return qsTr("Error")
        return qsTr("Unknown")
    }

    function sessionTone(state) {
        if (state === "error")
            return "failed"
        if (state === "idle" || state === "stopped")
            return "paused"
        return "available"
    }

    function captureStateLabel(state) {
        if (state === "permission_required" || state === "selecting_source")
            return qsTr("Waiting for portal permission or source")
        if (state === "starting")
            return qsTr("Starting capture")
        if (state === "active")
            return qsTr("Active")
        if (state === "cancelled")
            return qsTr("Selection cancelled")
        if (state === "permission_denied")
            return qsTr("Permission denied")
        if (state === "error" || state === "source_lost")
            return qsTr("Capture error")
        if (state === "unavailable")
            return qsTr("Unavailable")
        return qsTr("Stopped")
    }

    function ocrStateLabel(state) {
        if (state === "loading")
            return qsTr("Loading")
        if (state === "processing")
            return qsTr("Processing")
        if (state === "ready")
            return qsTr("Ready")
        if (state === "error")
            return qsTr("OCR error")
        return qsTr("Component missing")
    }

    function inferenceStateLabel(state, processingLabel, errorLabel) {
        if (state === "loading")
            return qsTr("Loading")
        if (state === "processing")
            return processingLabel
        if (state === "ready")
            return qsTr("Ready")
        if (state === "bypassed")
            return qsTr("Disabled")
        if (state === "error")
            return errorLabel
        return qsTr("Component missing")
    }

    function audioStateLabel(state) {
        if (state === "waiting")
            return qsTr("Waiting for audio")
        if (state === "speaking")
            return qsTr("Speaking")
        if (state === "ready")
            return qsTr("Ready")
        if (state === "error")
            return qsTr("Audio error")
        if (state === "stopped")
            return qsTr("Stopped")
        if (state === "unavailable")
            return qsTr("Unavailable")
        return qsTr("Component missing")
    }

    function formatBytes(raw) {
        if (raw === undefined || raw === null || Number(raw) < 0)
            return qsTr("Size not published")
        var amount = Number(raw)
        var units = ["B", "KiB", "MiB", "GiB"]
        var unit = 0
        while (amount >= 1024 && unit < units.length - 1) {
            amount /= 1024
            unit++
        }
        return (unit === 0 ? Math.round(amount) : amount.toFixed(1)) + " " + units[unit]
    }

    // Turning the override off clears the stored values, so the backend falls
    // back to the voice's own articulation instead of the last slider position.
    function setArticulationOverridden(enabled) {
        articulationOverridden = enabled
        saveSettings()
    }

    // Which pipeline variant negotiated, and whether the runtime can use
    // DMA-BUF at all. Distinguishes a DMA-BUF capable stream from a
    // system-memory fallback when diagnosing "unhandled format" failures.
    function formatCaptureFormat(session) {
        var variant = String(value(session, ["captureFormat"], ""))
        if (variant === "")
            return qsTr("Not negotiated")
        var failed = String(value(session, ["captureVariantsFailed"], ""))
        if (failed !== "")
            return qsTr("%1 (after %2 failed)").arg(variant).arg(failed)
        return variant
    }

    // Capture frames deliberately not sent to OCR. This is rate limiting, not
    // lost work: either the sampling interval had not elapsed, or an OCR request
    // was still in flight. A high number next to a healthy OCR count is normal.
    function formatSkippedFrames(session) {
        var total = Number(value(session, ["droppedFrames"], 0))
        var sampling = Number(value(session, ["droppedCaptureSampling"], 0))
        var busy = Number(value(session, ["droppedCaptureCoalesced"], 0))
        if (total <= 0)
            return "0"
        return qsTr("%1 (%2 sampling interval, %3 OCR busy)").arg(total).arg(sampling).arg(busy)
    }

    // Last playback outcome with expected versus actually played duration. A
    // truncated utterance shows a played time well below the expected time.
    function formatPlaybackResult(session) {
        var result = String(value(session, ["playbackLastResult"], ""))
        if (result === "")
            return qsTr("Not measured")
        var expected = value(session, ["playbackExpectedMs"], null)
        var actual = value(session, ["playbackActualMs"], null)
        if (expected === null || actual === null)
            return result
        return qsTr("%1 (played %2 of %3)").arg(result)
                .arg(formatLatency(actual)).arg(formatLatency(expected))
    }

    // Compact loss funnel. Shows every stage where a subtitle can disappear, so
    // a line lost before the audio queue is visible rather than invisible.
    function formatNarrationFunnel(session) {
        var funnel = value(session, ["narrationFunnel"], null)
        if (!funnel)
            return qsTr("Not measured")
        function count(key) { return Number(funnel[key] || 0) }
        return qsTr("seen %1 -> accepted %2 -> spoken %3 -> finished %4")
                .arg(count("observations"))
                .arg(count("accepted"))
                .arg(count("ttsSubmitted"))
                .arg(count("playedToCompletion"))
    }

    // Where lines were lost, in the pipeline's own decision vocabulary.
    function formatNarrationLosses(session) {
        var funnel = value(session, ["narrationFunnel"], null)
        if (!funnel)
            return qsTr("Not measured")
        function count(key) { return Number(funnel[key] || 0) }
        return qsTr("empty %1, low confidence %2, duplicate %3, abandoned %4, superseded %5")
                .arg(count("rejectedEmpty"))
                .arg(count("rejectedLowConfidence"))
                .arg(count("rejectedDuplicate"))
                .arg(count("candidateAbandoned"))
                .arg(count("supersededInAudioQueue"))
    }

    // Makes a silent variant fallback visible.
    function formatCaptureVariants(session) {
        var tried = String(value(session, ["captureVariantsTried"], ""))
        if (tried === "")
            return qsTr("None attempted")
        var glReady = value(session, ["captureDmabuf"], false) === true
        return tried + (glReady ? qsTr(" (GL available)") : qsTr(" (no GL)"))
    }

    function formatLatency(raw) {
        if (raw === undefined || raw === null || !isFinite(Number(raw)))
            return qsTr("Not measured")
        return qsTr("%1 ms").arg(Number(raw).toFixed(0))
    }

    // How long consensus waited between first seeing the subtitle and accepting
    // the phrase. Derived by subtraction from two values the session snapshot
    // already reports; nothing new is measured here.
    function formatConsensusWait(session) {
        var firstSeen = value(session, ["firstVisibleFrameToAudioStartMs"], null)
        var accepted = value(session, ["acceptedToAudioStartMs"], null)
        if (firstSeen === null || accepted === null)
            return qsTr("Not measured")
        if (!isFinite(Number(firstSeen)) || !isFinite(Number(accepted)))
            return qsTr("Not measured")
        return formatLatency(Math.max(0, Number(firstSeen) - Number(accepted)))
    }

    function runComponentAction(component) {
        if (!controller)
            return
        var componentId = String(value(component, ["componentId", "component_id"], ""))
        var state = String(value(component, ["state"], ""))
        if (state === "update_available"
                && typeof controller.updateNarratorComponent === "function")
            controller.updateNarratorComponent(componentId)
        else if (state === "available" && Boolean(value(component, ["managed"], false))
                 && typeof controller.removeNarratorComponent === "function")
            controller.removeNarratorComponent(componentId)
        else if (typeof controller.installNarratorComponent === "function")
            controller.installNarratorComponent(componentId)
    }

    onGameRowsChanged: Qt.callLater(ensureSelection)
    Component.onCompleted: Qt.callLater(ensureSelection)

    Connections {
        target: page.controller || null
        ignoreUnknownSignals: true

        function onNarratorChanged(gameId) {
            if (!gameId || String(gameId) === page.selectedGameId)
                page.refreshSession()
        }

        function onNarratorComponentsChanged() {
            page.loadSettings()
            page.refreshSession()
        }

        function onNarratorRegionPreviewChanged(gameId, preview) {
            page.applyRegionPreview(gameId, preview)
        }

        function onNarratorRegionSelectionChanged(gameId, selection) {
            page.applyNativeRegionSelection(gameId, selection)
        }
    }

    ScrollView {
        id: scroll
        anchors.fill: parent
        contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ScrollBar.vertical.policy: ScrollBar.AsNeeded

        ColumnLayout {
            width: scroll.availableWidth
            spacing: App.Theme.spacingLarge

            Item { Layout.preferredHeight: App.Theme.contentPadding - 4 }

            PageHeader {
                Layout.fillWidth: true
                Layout.leftMargin: App.Theme.contentPadding
                Layout.rightMargin: App.Theme.contentPadding
                title: qsTr("Narrator")
                subtitle: qsTr("Local English subtitle narration in Polish")

                StatusBadge {
                    text: page.sessionLabel(page.sessionStatus)
                    status: page.sessionTone(page.sessionStatus)
                }
            }

            SurfaceCard {
                Layout.fillWidth: true
                Layout.leftMargin: App.Theme.contentPadding
                Layout.rightMargin: App.Theme.contentPadding
                padding: 20

                contentItem: ColumnLayout {
                    spacing: 12

                    Label {
                        text: qsTr("Game")
                        color: App.Theme.text
                        font.pixelSize: 17
                        font.weight: Font.Bold
                    }

                    Label {
                        Layout.fillWidth: true
                        text: qsTr("Narrator settings are stored separately for each game. Models and voices are shared between games.")
                        color: App.Theme.textSecondary
                        font.pixelSize: App.Theme.fontCaption
                        wrapMode: Text.WordWrap
                    }

                    AppComboBox {
                        id: gameSelector
                        objectName: "narratorGameSelector"
                        Layout.fillWidth: true
                        model: page.gameLabels()
                        currentIndex: page.selectedGameIndex()
                        enabled: page.gameRows.length > 0 && !page.sessionActive
                        onActivated: function(index) {
                            if (index >= 0 && index < page.gameRows.length)
                                page.selectGame(page.gameRows[index].id)
                        }
                    }

                    Label {
                        visible: page.gameRows.length === 0
                        Layout.fillWidth: true
                        text: qsTr("No games are available. Scan or add a game first.")
                        color: App.Theme.warning
                        font.pixelSize: App.Theme.fontBody
                        wrapMode: Text.WordWrap
                    }
                }
            }

            GridLayout {
                id: narratorSettingsGrid
                Layout.fillWidth: true
                Layout.leftMargin: App.Theme.contentPadding
                Layout.rightMargin: App.Theme.contentPadding
                columns: page.width >= 1050 ? 2 : 1
                columnSpacing: App.Theme.spacingLarge
                rowSpacing: App.Theme.spacingLarge

                SurfaceCard {
                    Layout.fillWidth: narratorSettingsGrid.columns === 1
                    Layout.fillHeight: true
                    Layout.minimumWidth: narratorSettingsGrid.columns === 2 ? 320 : 0
                    Layout.preferredWidth: narratorSettingsGrid.columns === 2 ? 340 : -1
                    padding: 20

                    contentItem: ColumnLayout {
                        spacing: 10

                        Label {
                            text: qsTr("Narration")
                            color: App.Theme.text
                            font.pixelSize: 17
                            font.weight: Font.Bold
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 3

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 10

                                Label {
                                    Layout.fillWidth: true
                                    text: qsTr("Enabled for this game")
                                    color: App.Theme.text
                                    font.pixelSize: App.Theme.fontBody
                                    font.weight: Font.DemiBold
                                    wrapMode: Text.NoWrap
                                    elide: Text.ElideRight
                                }

                                AppSwitch {
                                    checked: page.narratorEnabled
                                    enabled: page.selectedGameId.length > 0 && !page.sessionActive
                                    onToggled: page.narratorEnabled = checked
                                }
                            }

                            Label {
                                Layout.fillWidth: true
                                text: qsTr("The session processes subtitles only while the selected game is running")
                                color: App.Theme.textSecondary
                                font.pixelSize: App.Theme.fontCaption
                                wrapMode: Text.WordWrap
                            }
                        }

                        Divider { Layout.fillWidth: true }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 6

                            Label {
                                Layout.fillWidth: true
                                text: qsTr("Subtitle source")
                                color: App.Theme.text
                                font.pixelSize: App.Theme.fontBody
                                font.weight: Font.DemiBold
                            }

                            AppComboBox {
                                property var values: ["auto", "ocr"]
                                Layout.fillWidth: true
                                model: [qsTr("Auto"), qsTr("OCR")]
                                currentIndex: page.indexOfValue(values, page.sourceMode, 0)
                                enabled: !page.sessionActive
                                onActivated: function(index) { page.sourceMode = values[index] }
                            }

                            Label {
                                Layout.fillWidth: true
                                text: qsTr("Auto can use a supported adapter later and keeps OCR as the universal fallback")
                                color: App.Theme.textSecondary
                                font.pixelSize: App.Theme.fontCaption
                                wrapMode: Text.WordWrap
                            }
                        }

                        Divider { Layout.fillWidth: true }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 6

                            Label {
                                Layout.fillWidth: true
                                text: qsTr("Capture source")
                                color: App.Theme.text
                                font.pixelSize: App.Theme.fontBody
                                font.weight: Font.DemiBold
                            }

                            AppComboBox {
                                property var values: ["window", "monitor"]
                                Layout.fillWidth: true
                                model: [qsTr("Window"), qsTr("Monitor")]
                                currentIndex: page.indexOfValue(values, page.captureSource, 0)
                                enabled: !page.sessionActive
                                onActivated: function(index) { page.captureSource = values[index] }
                            }

                            Label {
                                Layout.fillWidth: true
                                text: qsTr("The system portal asks which monitor or window may be captured")
                                color: App.Theme.textSecondary
                                font.pixelSize: App.Theme.fontCaption
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                }

                SurfaceCard {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    padding: 20

                    contentItem: ColumnLayout {
                        spacing: 10

                        Label {
                            text: qsTr("Voice and translation")
                            color: App.Theme.text
                            font.pixelSize: 17
                            font.weight: Font.Bold
                        }

                        Label {
                            Layout.fillWidth: true
                            text: qsTr("Subtitle language")
                            color: App.Theme.textMuted
                            font.pixelSize: App.Theme.fontCaption
                        }

                        AppComboBox {
                            property var values: ["english_to_polish", "polish"]
                            Layout.fillWidth: true
                            model: [
                                qsTr("English - translate to Polish"),
                                qsTr("Polish - read without translation")
                            ]
                            currentIndex: page.indexOfValue(
                                              values,
                                              page.subtitleLanguageMode,
                                              0)
                            enabled: !page.sessionActive
                            onActivated: function(index) {
                                if (index >= 0 && index < values.length)
                                    page.subtitleLanguageMode = values[index]
                            }
                        }

                        Label {
                            visible: page.subtitleLanguageMode !== "polish"
                            Layout.fillWidth: true
                            text: qsTr("Translation profile")
                            color: App.Theme.textMuted
                            font.pixelSize: App.Theme.fontCaption
                        }

                        AppComboBox {
                            visible: page.subtitleLanguageMode !== "polish"
                            property var values: page.optionIds(page.translationProfiles)
                            Layout.fillWidth: true
                            model: page.translationProfileLabels(page.translationProfiles)
                            currentIndex: page.indexOfValue(values, page.translationProfile, 0)
                            enabled: values.length > 0 && !page.sessionActive
                            onActivated: function(index) {
                                if (index >= 0 && index < values.length)
                                    page.translationProfile = values[index]
                            }
                        }

                        Label {
                            visible: page.subtitleLanguageMode !== "polish"
                                     && page.translationProfiles.length === 0
                            Layout.fillWidth: true
                            text: qsTr("Install the verified translation component to enable local English to Polish translation")
                            color: App.Theme.textSecondary
                            font.pixelSize: App.Theme.fontCaption
                            wrapMode: Text.WordWrap
                        }

                        Label {
                            Layout.fillWidth: true
                            text: qsTr("Polish voice")
                            color: App.Theme.textMuted
                            font.pixelSize: App.Theme.fontCaption
                        }

                        AppComboBox {
                            property var values: page.optionIds(page.voices)
                            Layout.fillWidth: true
                            model: page.optionLabels(page.voices)
                            currentIndex: page.indexOfValue(values, page.voiceId, 0)
                            enabled: values.length > 0 && !page.sessionActive
                            onActivated: function(index) {
                                if (index >= 0 && index < values.length)
                                    page.voiceId = values[index]
                            }
                        }

                        Label {
                            visible: page.voices.length === 0
                            Layout.fillWidth: true
                            text: qsTr("Install the verified Polish voice component to enable speech")
                            color: App.Theme.textSecondary
                            font.pixelSize: App.Theme.fontCaption
                            wrapMode: Text.WordWrap
                        }

                        RowLayout {
                            visible: page.voices.length > 0 && !page.selectedVoiceReady
                            Layout.fillWidth: true
                            spacing: 10

                            Label {
                                Layout.fillWidth: true
                                text: String(page.value(page.selectedVoice(), ["name", "id"], ""))
                                      + " - " + (page.selectedVoiceInstalled()
                                                   ? qsTr("Unavailable in this build")
                                                   : qsTr("Not installed"))
                                color: App.Theme.warning
                                font.pixelSize: App.Theme.fontCaption
                                wrapMode: Text.WordWrap
                            }

                            AppButton {
                                visible: !page.selectedVoiceInstalled()
                                compact: true
                                text: qsTr("Install")
                                iconSource: App.UiIcons.actionInstall
                                enabled: !page.sessionActive
                                         && page.selectedVoiceComponent() !== null
                                onClicked: page.installSelectedVoice()
                            }
                        }

                        Divider { Layout.fillWidth: true }

                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                Layout.fillWidth: true
                                text: qsTr("Volume")
                                color: App.Theme.text
                                font.pixelSize: App.Theme.fontBody
                            }
                            Label {
                                text: qsTr("%1%").arg(Math.round(page.narratorVolume * 100))
                                color: App.Theme.textSecondary
                                font.pixelSize: App.Theme.fontCaption
                            }
                        }

                        AppSlider {
                            Layout.fillWidth: true
                            from: 0
                            to: 1
                            stepSize: 0.05
                            value: page.narratorVolume
                            enabled: !page.sessionActive
                            onMoved: page.narratorVolume = value
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                Layout.fillWidth: true
                                text: qsTr("Speech speed")
                                color: App.Theme.text
                                font.pixelSize: App.Theme.fontBody
                            }
                            Label {
                                text: qsTr("%1x").arg(page.speechRate.toFixed(2))
                                color: App.Theme.textSecondary
                                font.pixelSize: App.Theme.fontCaption
                            }
                        }

                        AppSlider {
                            Layout.fillWidth: true
                            from: 0.5
                            to: 2.0
                            stepSize: 0.05
                            value: page.speechRate
                            enabled: !page.sessionActive
                            onMoved: page.speechRate = value
                        }

                        // Advanced Piper inference overrides. Unset by default,
                        // in which case each voice's own configured value is
                        // used. Exposed so a different articulation can be
                        // tried; no outcome is promised here.
                        AppSwitch {
                            id: articulationToggle
                            Layout.fillWidth: true
                            text: qsTr("Advanced voice articulation")
                            checked: page.articulationOverridden
                            enabled: !page.sessionActive
                            onToggled: page.setArticulationOverridden(checked)
                        }

                        Label {
                            Layout.fillWidth: true
                            visible: articulationToggle.checked
                            text: qsTr("Piper inference parameters. Lower values reduce variation. Leave this off to use each voice's own values.")
                            color: App.Theme.textSecondary
                            font.pixelSize: App.Theme.fontCaption
                            wrapMode: Text.WordWrap
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            visible: articulationToggle.checked
                            Label {
                                Layout.fillWidth: true
                                text: qsTr("Phoneme duration variation")
                                color: App.Theme.text
                                font.pixelSize: App.Theme.fontBody
                            }
                            Label {
                                text: page.noiseWScale.toFixed(3)
                                color: App.Theme.textSecondary
                                font.pixelSize: App.Theme.fontCaption
                            }
                        }

                        AppSlider {
                            Layout.fillWidth: true
                            visible: articulationToggle.checked
                            from: 0.0
                            to: 2.0
                            stepSize: 0.025
                            value: page.noiseWScale
                            enabled: !page.sessionActive
                            onMoved: page.noiseWScale = value
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            visible: articulationToggle.checked
                            Label {
                                Layout.fillWidth: true
                                text: qsTr("Acoustic variation")
                                color: App.Theme.text
                                font.pixelSize: App.Theme.fontBody
                            }
                            Label {
                                text: page.noiseScale.toFixed(3)
                                color: App.Theme.textSecondary
                                font.pixelSize: App.Theme.fontCaption
                            }
                        }

                        AppSlider {
                            Layout.fillWidth: true
                            visible: articulationToggle.checked
                            from: 0.0
                            to: 2.0
                            stepSize: 0.025
                            value: page.noiseScale
                            enabled: !page.sessionActive
                            onMoved: page.noiseScale = value
                        }
                    }
                }
            }

            SurfaceCard {
                Layout.fillWidth: true
                Layout.leftMargin: App.Theme.contentPadding
                Layout.rightMargin: App.Theme.contentPadding
                padding: 20

                contentItem: ColumnLayout {
                    spacing: 12

                    RowLayout {
                        Layout.fillWidth: true
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            Label {
                                text: qsTr("Subtitle region")
                                color: App.Theme.text
                                font.pixelSize: 17
                                font.weight: Font.Bold
                            }
                            Label {
                                Layout.fillWidth: true
                                text: qsTr("Only this normalized part of the selected image is sent to OCR")
                                color: App.Theme.textSecondary
                                font.pixelSize: App.Theme.fontCaption
                                wrapMode: Text.WordWrap
                            }
                        }
                        AppButton {
                            text: page.regionPreviewLoading
                                  ? qsTr("Cancel capture")
                                  : qsTr("Select subtitle area on game")
                            kind: page.regionPreviewLoading ? "ghost" : "primary"
                            compact: true
                            enabled: page.selectedGameId.length > 0
                            iconSource: page.regionPreviewLoading
                                        ? App.UiIcons.actionCancel
                                        : App.UiIcons.actionScan
                            onClicked: page.regionPreviewLoading
                                       ? page.cancelRegionPreview()
                                       : page.requestRegionPreview()
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: Math.max(150, width * 0.22)
                        Layout.maximumHeight: 250
                        radius: App.Theme.radiusMedium
                        color: App.Theme.backgroundElevated
                        border.width: 1
                        border.color: App.Theme.border
                        clip: true

                        Image {
                            id: currentRegionPreviewImage
                            anchors.fill: parent
                            source: page.regionPreviewSource
                            fillMode: Image.PreserveAspectFit
                            asynchronous: true
                            cache: false
                        }

                        Label {
                            anchors.centerIn: parent
                            visible: currentRegionPreviewImage.status !== Image.Ready
                            text: page.regionPreviewLoading
                                  ? qsTr("Waiting for the selected game frame...")
                                  : qsTr("Capture a game frame to preview the OCR region")
                            color: App.Theme.textMuted
                            font.pixelSize: App.Theme.fontBody
                        }

                        Rectangle {
                            readonly property real fittedWidth: currentRegionPreviewImage.status === Image.Ready
                                                                 ? currentRegionPreviewImage.paintedWidth : parent.width
                            readonly property real fittedHeight: currentRegionPreviewImage.status === Image.Ready
                                                                  ? currentRegionPreviewImage.paintedHeight : parent.height
                            readonly property real fittedX: (parent.width - fittedWidth) / 2
                            readonly property real fittedY: (parent.height - fittedHeight) / 2
                            x: fittedX + page.cropX * fittedWidth
                            y: fittedY + page.cropY * fittedHeight
                            width: page.cropWidth * fittedWidth
                            height: page.cropHeight * fittedHeight
                            color: App.Theme.accentSoft
                            border.width: 2
                            border.color: App.Theme.accent

                            Label {
                                anchors.centerIn: parent
                                text: qsTr("OCR region")
                                color: App.Theme.accent
                                font.pixelSize: App.Theme.fontCaption
                                font.weight: Font.Bold
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        Label {
                            Layout.fillWidth: true
                            text: page.regionPreviewSourceWidth > 0
                                  ? qsTr("Source: %1x%2    OCR region: %3x%4")
                                        .arg(page.regionPreviewSourceWidth)
                                        .arg(page.regionPreviewSourceHeight)
                                        .arg(Math.round(page.regionPreviewSourceWidth * page.cropWidth))
                                        .arg(Math.round(page.regionPreviewSourceHeight * page.cropHeight))
                                  : qsTr("Source and OCR dimensions will appear after capturing a frame")
                            color: App.Theme.textSecondary
                            font.pixelSize: App.Theme.fontCaption
                            wrapMode: Text.WordWrap
                        }
                        AppButton {
                            text: page.advancedRegionVisible
                                  ? qsTr("Hide fine tuning") : qsTr("Advanced fine tuning")
                            compact: true
                            kind: "ghost"
                            iconSource: App.UiIcons.actionAdvancedSettings
                            onClicked: page.advancedRegionVisible = !page.advancedRegionVisible
                        }
                    }

                    Label {
                        visible: page.regionPreviewError.length > 0
                        Layout.fillWidth: true
                        text: page.regionPreviewError
                        color: App.Theme.danger
                        wrapMode: Text.WordWrap
                    }

                    GridLayout {
                        visible: page.advancedRegionVisible
                        Layout.fillWidth: true
                        columns: page.width >= 900 ? 2 : 1
                        columnSpacing: 22
                        rowSpacing: 8

                        ColumnLayout {
                            Layout.fillWidth: true
                            RowLayout {
                                Layout.fillWidth: true
                                Label { Layout.fillWidth: true; text: qsTr("Left"); color: App.Theme.textSecondary }
                                Label { text: qsTr("%1%").arg(Math.round(page.cropX * 100)); color: App.Theme.textMuted }
                            }
                            AppSlider {
                                Layout.fillWidth: true
                                from: 0
                                to: Math.max(0, 1 - page.cropWidth)
                                stepSize: 0.01
                                value: page.cropX
                                enabled: !page.sessionActive
                                onMoved: page.cropX = value
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            RowLayout {
                                Layout.fillWidth: true
                                Label { Layout.fillWidth: true; text: qsTr("Top"); color: App.Theme.textSecondary }
                                Label { text: qsTr("%1%").arg(Math.round(page.cropY * 100)); color: App.Theme.textMuted }
                            }
                            AppSlider {
                                Layout.fillWidth: true
                                from: 0
                                to: Math.max(0, 1 - page.cropHeight)
                                stepSize: 0.01
                                value: page.cropY
                                enabled: !page.sessionActive
                                onMoved: page.cropY = value
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            RowLayout {
                                Layout.fillWidth: true
                                Label { Layout.fillWidth: true; text: qsTr("Width"); color: App.Theme.textSecondary }
                                Label { text: qsTr("%1%").arg(Math.round(page.cropWidth * 100)); color: App.Theme.textMuted }
                            }
                            AppSlider {
                                Layout.fillWidth: true
                                from: 0.05
                                to: Math.max(0.05, 1 - page.cropX)
                                stepSize: 0.01
                                value: page.cropWidth
                                enabled: !page.sessionActive
                                onMoved: page.cropWidth = value
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            RowLayout {
                                Layout.fillWidth: true
                                Label { Layout.fillWidth: true; text: qsTr("Height"); color: App.Theme.textSecondary }
                                Label { text: qsTr("%1%").arg(Math.round(page.cropHeight * 100)); color: App.Theme.textMuted }
                            }
                            AppSlider {
                                Layout.fillWidth: true
                                from: 0.05
                                to: Math.max(0.05, 1 - page.cropY)
                                stepSize: 0.01
                                value: page.cropHeight
                                enabled: !page.sessionActive
                                onMoved: page.cropHeight = value
                            }
                        }
                    }

                    RowLayout {
                        visible: page.advancedRegionVisible
                        Layout.fillWidth: true
                        Item { Layout.fillWidth: true }
                        AppButton {
                            text: qsTr("Reset to bottom area")
                            compact: true
                            enabled: !page.sessionActive
                            onClicked: page.resetSubtitleRegion()
                        }
                    }
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.leftMargin: App.Theme.contentPadding
                Layout.rightMargin: App.Theme.contentPadding
                spacing: 10

                Label {
                    text: qsTr("Optional components")
                    color: App.Theme.text
                    font.pixelSize: 20
                    font.weight: Font.Bold
                }

                Label {
                    Layout.fillWidth: true
                    text: qsTr("OCR, translation, and voice models are installed in the user data directory and shared by all games. No model is bundled with the base application.")
                    color: App.Theme.textSecondary
                    font.pixelSize: App.Theme.fontCaption
                    wrapMode: Text.WordWrap
                }

                GridLayout {
                    Layout.fillWidth: true
                    columns: page.width >= 1050 ? 2 : 1
                    columnSpacing: 12
                    rowSpacing: 12

                    Repeater {
                        model: page.componentsData || []

                        delegate: SurfaceCard {
                            id: componentCard
                            required property var modelData
                            Layout.fillWidth: true
                            Layout.preferredHeight: componentColumn.implicitHeight + 36
                            padding: 18

                            readonly property string componentState: String(
                                                                         page.value(
                                                                             modelData,
                                                                             ["state"],
                                                                             "unknown"))
                            readonly property bool managed: Boolean(page.value(modelData, ["managed"], false))
                            readonly property bool installable: Boolean(page.value(
                                                                            modelData,
                                                                            ["installable", "canInstall"],
                                                                            false))

                            contentItem: ColumnLayout {
                                id: componentColumn
                                spacing: 8

                                RowLayout {
                                    Layout.fillWidth: true
                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        spacing: 2
                                        Label {
                                            Layout.fillWidth: true
                                            text: page.componentName(componentCard.modelData)
                                            color: App.Theme.text
                                            font.pixelSize: App.Theme.fontBodyLarge
                                            font.weight: Font.Bold
                                            wrapMode: Text.WordWrap
                                        }
                                        Label {
                                            text: page.componentKind(String(page.value(componentCard.modelData, ["kind"], "")))
                                            color: App.Theme.textMuted
                                            font.pixelSize: App.Theme.fontCaption
                                        }
                                    }
                                    StatusBadge {
                                        text: page.componentStateLabel(componentCard.componentState)
                                        status: page.componentTone(componentCard.componentState)
                                    }
                                }

                                Label {
                                    visible: page.componentDescription(componentCard.modelData).length > 0
                                    Layout.fillWidth: true
                                    text: page.componentDescription(componentCard.modelData)
                                    color: App.Theme.textSecondary
                                    font.pixelSize: App.Theme.fontCaption
                                    wrapMode: Text.WordWrap
                                }

                                Label {
                                    visible: String(page.value(componentCard.modelData, ["message"], "")).length > 0
                                    Layout.fillWidth: true
                                    text: App.I18n.message(String(page.value(componentCard.modelData, ["message"], "")))
                                    color: componentCard.componentState === "error"
                                           ? App.Theme.danger : App.Theme.textMuted
                                    font.pixelSize: App.Theme.fontCaption
                                    wrapMode: Text.WordWrap
                                }

                                Flow {
                                    Layout.fillWidth: true
                                    spacing: 14
                                    Label {
                                        text: qsTr("Download: %1").arg(page.formatBytes(
                                                                          page.value(
                                                                              componentCard.modelData,
                                                                              ["downloadSizeBytes", "download_size_bytes"],
                                                                              null)))
                                        color: App.Theme.textSecondary
                                        font.pixelSize: App.Theme.fontCaption
                                    }
                                    Label {
                                        visible: page.value(componentCard.modelData, ["installedSizeBytes", "installed_size_bytes"], null) !== null
                                        text: qsTr("Installed: %1").arg(page.formatBytes(
                                                                           page.value(
                                                                               componentCard.modelData,
                                                                               ["installedSizeBytes", "installed_size_bytes"],
                                                                               null)))
                                        color: App.Theme.textSecondary
                                        font.pixelSize: App.Theme.fontCaption
                                    }
                                    Label {
                                        visible: String(page.value(componentCard.modelData, ["version"], "")).length > 0
                                        text: qsTr("Version: %1").arg(page.value(componentCard.modelData, ["version"], ""))
                                        color: App.Theme.textSecondary
                                        font.pixelSize: App.Theme.fontCaption
                                    }
                                    Label {
                                        visible: String(page.value(componentCard.modelData, ["licenseId", "license_id"], "")).length > 0
                                        text: qsTr("License: %1").arg(page.value(componentCard.modelData, ["licenseId", "license_id"], ""))
                                        color: App.Theme.textSecondary
                                        font.pixelSize: App.Theme.fontCaption
                                    }
                                    Label {
                                        visible: String(page.value(componentCard.modelData, ["runtimeLicenseId"], "")).length > 0
                                        text: qsTr("Runtime license: %1").arg(page.value(componentCard.modelData, ["runtimeLicenseId"], ""))
                                        color: App.Theme.textSecondary
                                        font.pixelSize: App.Theme.fontCaption
                                    }
                                    Label {
                                        visible: String(page.value(componentCard.modelData, ["artifactLicenseId"], "")).length > 0
                                        text: qsTr("Model license: %1").arg(page.value(componentCard.modelData, ["artifactLicenseId"], ""))
                                        color: App.Theme.textSecondary
                                        font.pixelSize: App.Theme.fontCaption
                                    }
                                    Label {
                                        visible: String(page.value(componentCard.modelData, ["attribution"], "")).length > 0
                                        text: qsTr("Attribution: %1").arg(page.value(componentCard.modelData, ["attribution"], ""))
                                        color: App.Theme.textSecondary
                                        font.pixelSize: App.Theme.fontCaption
                                    }
                                }

                                AppButton {
                                    Layout.alignment: Qt.AlignRight
                                    compact: true
                                    text: componentCard.componentState === "update_available"
                                          ? qsTr("Update")
                                          : componentCard.componentState === "available" && componentCard.managed
                                            ? qsTr("Remove") : qsTr("Install")
                                    kind: componentCard.componentState === "available" && componentCard.managed
                                          ? "danger" : "secondary"
                                    iconSource: componentCard.componentState === "update_available"
                                                ? App.UiIcons.actionDownload
                                                : componentCard.componentState === "available" && componentCard.managed
                                                  ? App.UiIcons.actionRemove : App.UiIcons.actionInstall
                                    enabled: !page.sessionActive
                                             && (componentCard.componentState === "update_available"
                                                 || (componentCard.componentState === "available" && componentCard.managed)
                                                 || componentCard.installable)
                                    toolTip: enabled ? "" : qsTr("No verified download is configured for this component")
                                    onClicked: page.runComponentAction(componentCard.modelData)
                                }
                            }
                        }
                    }
                }
            }

            SurfaceCard {
                Layout.fillWidth: true
                Layout.leftMargin: App.Theme.contentPadding
                Layout.rightMargin: App.Theme.contentPadding
                padding: 20

                contentItem: ColumnLayout {
                    spacing: 10

                    RowLayout {
                        Layout.fillWidth: true
                        Label {
                            Layout.fillWidth: true
                            text: qsTr("Narrator session")
                            color: App.Theme.text
                            font.pixelSize: 17
                            font.weight: Font.Bold
                        }
                        StatusBadge {
                            text: page.sessionLabel(page.sessionStatus)
                            status: page.sessionTone(page.sessionStatus)
                        }
                    }

                    Label {
                        visible: String(page.value(page.sessionData, ["message"], "")).length > 0
                        Layout.fillWidth: true
                        text: App.I18n.message(String(page.value(page.sessionData, ["message"], "")))
                        color: page.sessionStatus === "error" ? App.Theme.danger : App.Theme.textSecondary
                        font.pixelSize: App.Theme.fontBody
                        wrapMode: Text.WordWrap
                    }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: page.width >= 950 ? 3 : 1
                        columnSpacing: 18
                        rowSpacing: 12

                        ColumnLayout {
                            Layout.fillWidth: true
                            Label { text: qsTr("Last accepted subtitle"); color: App.Theme.textMuted; font.pixelSize: App.Theme.fontCaption }
                            Label {
                                Layout.fillWidth: true
                                text: String(page.value(page.sessionData, ["lastDetectedText", "last_detected_text"], qsTr("None")))
                                color: App.Theme.text
                                font.pixelSize: App.Theme.fontBody
                                wrapMode: Text.WordWrap
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            Label { text: qsTr("Polish translation"); color: App.Theme.textMuted; font.pixelSize: App.Theme.fontCaption }
                            Label {
                                Layout.fillWidth: true
                                text: String(page.value(page.sessionData, ["lastTranslation", "last_translation"], qsTr("None")))
                                color: App.Theme.text
                                font.pixelSize: App.Theme.fontBody
                                wrapMode: Text.WordWrap
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            Label { text: qsTr("Last spoken phrase"); color: App.Theme.textMuted; font.pixelSize: App.Theme.fontCaption }
                            Label {
                                Layout.fillWidth: true
                                text: String(page.value(page.sessionData, ["lastSpokenText", "last_spoken_text"], qsTr("None")))
                                color: App.Theme.text
                                font.pixelSize: App.Theme.fontBody
                                wrapMode: Text.WordWrap
                            }
                        }
                    }

                    Divider { Layout.fillWidth: true }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: page.width >= 950 ? 3 : 1
                        columnSpacing: 18
                        rowSpacing: 8

                        ColumnLayout {
                            Layout.fillWidth: true
                            Label { text: qsTr("Capture status"); color: App.Theme.textMuted; font.pixelSize: App.Theme.fontCaption }
                            Label {
                                Layout.fillWidth: true
                                text: page.captureStateLabel(String(page.value(page.sessionData, ["captureState"], "stopped")))
                                color: App.Theme.text
                                wrapMode: Text.WordWrap
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            Label { text: qsTr("Translation status"); color: App.Theme.textMuted; font.pixelSize: App.Theme.fontCaption }
                            Label {
                                Layout.fillWidth: true
                                text: page.inferenceStateLabel(String(page.value(page.sessionData, ["translationStatus"], "component_missing")), qsTr("Translating"), qsTr("Translation error"))
                                color: App.Theme.text
                                wrapMode: Text.WordWrap
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            Label { text: qsTr("Speech status"); color: App.Theme.textMuted; font.pixelSize: App.Theme.fontCaption }
                            Label {
                                Layout.fillWidth: true
                                text: page.inferenceStateLabel(String(page.value(page.sessionData, ["ttsStatus"], "component_missing")), qsTr("Generating speech"), qsTr("Speech error"))
                                color: App.Theme.text
                                wrapMode: Text.WordWrap
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            Label { text: qsTr("Audio status"); color: App.Theme.textMuted; font.pixelSize: App.Theme.fontCaption }
                            Label {
                                Layout.fillWidth: true
                                text: page.audioStateLabel(String(page.value(page.sessionData, ["audioStatus"], "component_missing")))
                                color: App.Theme.text
                                wrapMode: Text.WordWrap
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            Label { text: qsTr("OCR status"); color: App.Theme.textMuted; font.pixelSize: App.Theme.fontCaption }
                            Label {
                                Layout.fillWidth: true
                                text: page.ocrStateLabel(String(page.value(page.sessionData, ["ocrStatus"], "component_missing")))
                                color: App.Theme.text
                                wrapMode: Text.WordWrap
                            }
                        }
                    }

                    Divider { Layout.fillWidth: true }

                    Flow {
                        Layout.fillWidth: true
                        spacing: 18
                        Label { text: qsTr("Capture: %1").arg(page.formatLatency(page.value(page.sessionData, ["captureMs", "capture_ms"], null))); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("OCR total: %1").arg(page.formatLatency(page.value(page.sessionData, ["ocrMs", "ocr_ms"], null))); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("OCR recognition: %1").arg(page.formatLatency(page.value(page.sessionData, ["ocrRecognitionMs"], null))); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("OCR image + PNG: %1").arg(page.formatLatency(page.value(page.sessionData, ["ocrPreprocessingMs"], null))); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("OCR worker wait: %1").arg(page.formatLatency(page.value(page.sessionData, ["ocrWorkerLockWaitMs"], null))); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("Translation: %1").arg(page.formatLatency(page.value(page.sessionData, ["translationMs", "translation_ms"], null))); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("Speech: %1").arg(page.formatLatency(page.value(page.sessionData, ["ttsMs", "tts_ms"], null))); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("Audio start: %1").arg(page.formatLatency(page.value(page.sessionData, ["audioStartMs", "audio_start_ms"], null))); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("Source capture: %1x%2").arg(page.value(page.sessionData, ["captureWidth"], 0)).arg(page.value(page.sessionData, ["captureHeight"], 0)); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("OCR ROI: %1x%2").arg(page.value(page.sessionData, ["ocrRoiWidth"], 0)).arg(page.value(page.sessionData, ["ocrRoiHeight"], 0)); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("Capture to text: %1").arg(page.formatLatency(page.value(page.sessionData, ["totalCaptureToTextMs"], null))); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("Subtitle on screen to speech: %1").arg(page.formatLatency(page.value(page.sessionData, ["firstVisibleFrameToAudioStartMs"], null))); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("Consensus wait: %1").arg(page.formatConsensusWait(page.sessionData)); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("Phrase accepted to speech: %1").arg(page.formatLatency(page.value(page.sessionData, ["acceptedToAudioStartMs"], null))); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("Confirming frame to speech: %1").arg(page.formatLatency(page.value(page.sessionData, ["totalCaptureToAudioStartMs"], null))); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("OCR runs: %1").arg(page.value(page.sessionData, ["ocrExecutionCount"], 0)); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("Frames skipped by rate limit: %1").arg(page.formatSkippedFrames(page.sessionData)); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("Subtitle funnel: %1").arg(page.formatNarrationFunnel(page.sessionData)); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("Lost at: %1").arg(page.formatNarrationLosses(page.sessionData)); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("Superseded in audio queue: %1").arg(page.value(page.sessionData, ["audioSupersessions"], 0)); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("Playback completed: %1").arg(page.value(page.sessionData, ["playbackCompleted"], 0)); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("Playback interrupted: %1").arg(page.value(page.sessionData, ["playbackInterrupted"], 0)); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("Last playback: %1").arg(page.formatPlaybackResult(page.sessionData)); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("Frames received: %1").arg(page.value(page.sessionData, ["captureFramesReceived"], 0)); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("Capture format: %1").arg(page.formatCaptureFormat(page.sessionData)); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("Capture variants tried: %1").arg(page.formatCaptureVariants(page.sessionData)); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("Capture stream errors: %1").arg(page.value(page.sessionData, ["captureStreamErrors"], 0)); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label { text: qsTr("Capture restarts: %1").arg(page.value(page.sessionData, ["captureRestarts"], 0)); color: App.Theme.textSecondary; font.pixelSize: App.Theme.fontCaption }
                        Label {
                            text: qsTr("Subtitle region") + ": x="
                                  + page.cropX.toFixed(3) + " y="
                                  + page.cropY.toFixed(3) + " w="
                                  + page.cropWidth.toFixed(3) + " h="
                                  + page.cropHeight.toFixed(3)
                            color: App.Theme.textSecondary
                            font.pixelSize: App.Theme.fontCaption
                        }
                        Label {
                            visible: page.value(page.sessionData, ["lastDetectedAgeSeconds"], null) !== null
                            text: qsTr("Last phrase: %1 s ago").arg(Number(page.value(page.sessionData, ["lastDetectedAgeSeconds"], 0)).toFixed(1))
                            color: App.Theme.textSecondary
                            font.pixelSize: App.Theme.fontCaption
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 3

                        Label {
                            Layout.fillWidth: true
                            text: qsTr("OCR diagnostics")
                            color: App.Theme.textMuted
                            font.pixelSize: App.Theme.fontCaption
                            font.weight: Font.DemiBold
                        }
                        Label {
                            Layout.fillWidth: true
                            text: qsTr("Backend: %1 · confidence: %2 · decision: %3 · match: %4 · candidate: %5/%6")
                                .arg(String(page.value(page.sessionData, ["ocrBackend"], qsTr("Unknown"))))
                                .arg(page.value(page.sessionData, ["ocrConfidence"], null) === null
                                     ? qsTr("Not measured")
                                     : Number(page.value(page.sessionData, ["ocrConfidence"], 0)).toFixed(3))
                                .arg(String(page.value(page.sessionData, ["lastOcrGateDecision"], qsTr("None"))))
                                .arg(String(page.value(page.sessionData, ["ocrCandidateMatchKind"], qsTr("None"))))
                                .arg(page.value(page.sessionData, ["ocrCandidateObservationCount"], 0))
                                .arg(page.value(page.sessionData, ["ocrCandidateRequiredObservations"], 2))
                            color: App.Theme.textSecondary
                            font.pixelSize: App.Theme.fontCaption
                            wrapMode: Text.WordWrap
                        }
                        Label {
                            visible: String(page.value(page.sessionData, ["ocrDebugCapturePath"], "")).length > 0
                            Layout.fillWidth: true
                            text: qsTr("Debug capture: %1").arg(String(page.value(page.sessionData, ["ocrDebugCapturePath"], "")))
                            color: App.Theme.textSecondary
                            font.pixelSize: App.Theme.fontCaption
                            wrapMode: Text.WrapAnywhere
                        }
                        Label {
                            Layout.fillWidth: true
                            text: qsTr("Raw OCR: %1").arg(String(page.value(page.sessionData, ["lastRawOcrText"], qsTr("None"))))
                            color: App.Theme.textSecondary
                            font.pixelSize: App.Theme.fontCaption
                            wrapMode: Text.WordWrap
                        }
                        Label {
                            Layout.fillWidth: true
                            text: qsTr("ROI decision: %1 · localized change: %2")
                                .arg(String(page.value(page.sessionData, ["lastVisualChangeDecision"], qsTr("None"))))
                                .arg(page.value(page.sessionData, ["lastVisualChangeScore"], null) === null
                                     ? qsTr("Not measured")
                                     : Number(page.value(page.sessionData, ["lastVisualChangeScore"], 0)).toFixed(4))
                            color: App.Theme.textSecondary
                            font.pixelSize: App.Theme.fontCaption
                            wrapMode: Text.WordWrap
                        }
                        Label {
                            Layout.fillWidth: true
                            text: qsTr("Filtered OCR: %1")
                                .arg(String(page.value(page.sessionData, ["lastFilteredOcrText"], qsTr("None"))))
                            color: App.Theme.textSecondary
                            font.pixelSize: App.Theme.fontCaption
                            wrapMode: Text.WordWrap
                        }
                        Label {
                            Layout.fillWidth: true
                            text: qsTr("Normalized OCR: %1 · rejection: %2")
                                .arg(String(page.value(page.sessionData, ["lastNormalizedOcrText"], qsTr("None"))))
                                .arg(String(page.value(page.sessionData, ["lastOcrRejectionReason"], qsTr("None"))))
                            color: App.Theme.textSecondary
                            font.pixelSize: App.Theme.fontCaption
                            wrapMode: Text.WordWrap
                        }

                        AppButton {
                            text: page.recentOcrDecisionsExpanded
                                  ? qsTr("Hide recent OCR decisions")
                                  : qsTr("Recent OCR decisions (%1)").arg(
                                        page.value(page.sessionData, ["ocrDecisionHistory"], []).length)
                            kind: "ghost"
                            compact: true
                            onClicked: page.recentOcrDecisionsExpanded = !page.recentOcrDecisionsExpanded
                        }

                        ColumnLayout {
                            objectName: "recentOcrDecisionList"
                            Layout.fillWidth: true
                            visible: page.recentOcrDecisionsExpanded
                            spacing: 8

                            Repeater {
                                model: page.value(page.sessionData, ["ocrDecisionHistory"], [])

                                delegate: Rectangle {
                                    id: decisionRow
                                    required property var modelData
                                    Layout.fillWidth: true
                                    implicitHeight: decisionDetails.implicitHeight + 14
                                    radius: App.Theme.radiusSmall
                                    color: App.Theme.surfaceRaised
                                    border.width: 1
                                    border.color: App.Theme.border

                                    ColumnLayout {
                                        id: decisionDetails
                                        anchors.fill: parent
                                        anchors.margins: 7
                                        spacing: 2

                                        Label {
                                            Layout.fillWidth: true
                                            text: qsTr("%1 s · confidence %2 · candidate %3/%4 · %5")
                                                .arg(Number(decisionRow.modelData.ageSeconds || 0).toFixed(1))
                                                .arg(decisionRow.modelData.confidence === null
                                                     || decisionRow.modelData.confidence === undefined
                                                     ? qsTr("Not measured")
                                                     : Number(decisionRow.modelData.confidence).toFixed(3))
                                                .arg(Number(decisionRow.modelData.candidateObservationCount || 0))
                                                .arg(Number(decisionRow.modelData.candidateRequiredObservations || 2))
                                                .arg(String(decisionRow.modelData.decision || qsTr("None")))
                                            color: App.Theme.text
                                            font.pixelSize: App.Theme.fontCaption
                                            font.weight: Font.DemiBold
                                            wrapMode: Text.WordWrap
                                        }
                                        Label {
                                            Layout.fillWidth: true
                                            text: qsTr("Raw: %1").arg(String(decisionRow.modelData.rawText || qsTr("None")))
                                            color: App.Theme.textSecondary
                                            font.pixelSize: App.Theme.fontCaption
                                            wrapMode: Text.WordWrap
                                        }
                                        Label {
                                            Layout.fillWidth: true
                                            text: qsTr("Filtered: %1 · normalized: %2")
                                                .arg(String(decisionRow.modelData.filteredText || qsTr("None")))
                                                .arg(String(decisionRow.modelData.normalizedText || qsTr("None")))
                                            color: App.Theme.textSecondary
                                            font.pixelSize: App.Theme.fontCaption
                                            wrapMode: Text.WordWrap
                                        }
                                        Label {
                                            Layout.fillWidth: true
                                            text: qsTr("Reason: %1 · match: %2 · replaced: %3 · TTS: %4 · ROI: %5x%6 · %7 / %8")
                                                .arg(String(decisionRow.modelData.rejectionReason || qsTr("None")))
                                                .arg(String(decisionRow.modelData.candidateMatchKind || qsTr("None")))
                                                .arg(decisionRow.modelData.candidateReplaced ? qsTr("yes") : qsTr("no"))
                                                .arg(decisionRow.modelData.ttsSubmitted ? qsTr("submitted") : qsTr("not submitted"))
                                                .arg(Number(decisionRow.modelData.roiWidth || 0))
                                                .arg(Number(decisionRow.modelData.roiHeight || 0))
                                                .arg(String(decisionRow.modelData.backend || qsTr("Unknown")))
                                                .arg(page.formatLatency(decisionRow.modelData.recognitionMs))
                                            color: App.Theme.textMuted
                                            font.pixelSize: App.Theme.fontCaption
                                            wrapMode: Text.WordWrap
                                        }
                                        Label {
                                            Layout.fillWidth: true
                                            text: qsTr("Tokens: %1/%2 · lines: %3 · dropped: %4 · minimum confidence: %5 · geometry: %6 · strong short evidence: %7 · visual: %8 · filter: %9")
                                                .arg(Number(decisionRow.modelData.includedTokenCount || 0))
                                                .arg(Number(decisionRow.modelData.tokenCount || 0))
                                                .arg(Number(decisionRow.modelData.lineCount || 0))
                                                .arg(Number(decisionRow.modelData.droppedTokenCount || 0))
                                                .arg(decisionRow.modelData.minimumTokenConfidence === null
                                                     || decisionRow.modelData.minimumTokenConfidence === undefined
                                                     ? qsTr("Not measured")
                                                     : Number(decisionRow.modelData.minimumTokenConfidence).toFixed(3))
                                                .arg(decisionRow.modelData.geometryCoherent ? qsTr("coherent") : qsTr("uncertain"))
                                                .arg(decisionRow.modelData.cleanShortPhraseEvidence ? qsTr("yes") : qsTr("no"))
                                                .arg(String(decisionRow.modelData.visualChangeDecision || qsTr("Unknown")))
                                                .arg(String(decisionRow.modelData.filterSummary || qsTr("unchanged")))
                                            color: App.Theme.textMuted
                                            font.pixelSize: App.Theme.fontCaption
                                            wrapMode: Text.WordWrap
                                        }
                                    }
                                }
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 10
                        Item { Layout.fillWidth: true }
                        AppButton {
                            text: qsTr("Save settings")
                            enabled: page.selectedGameId.length > 0 && !page.sessionActive
                            onClicked: page.saveSettings()
                        }
                        AppButton {
                            text: page.sessionActive ? qsTr("Stop narrator") : qsTr("Start narrator")
                            kind: page.sessionActive ? "danger" : "primary"
                            enabled: page.sessionActive
                                     || (page.selectedGameId.length > 0
                                         && page.narratorEnabled
                                         && page.componentsReady
                                         && page.selectedVoiceReady)
                            toolTip: enabled ? "" : qsTr("Enable the narrator and install all required local narrator components first")
                            onClicked: page.sessionActive
                                       ? page.stopNarrator() : page.startNarrator()
                        }
                    }
                }
            }

            Item { Layout.preferredHeight: App.Theme.contentPadding }
        }
    }

}
