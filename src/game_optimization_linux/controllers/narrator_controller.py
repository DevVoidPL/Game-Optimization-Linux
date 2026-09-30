"""QML-facing narrator settings and session boundary."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
import hashlib
import logging
import time
from typing import TYPE_CHECKING, Any, Mapping

from game_optimization_linux.models import Game
from game_optimization_linux.models.narrator import (
    CaptureFrame,
    CaptureState,
    NarratorGameSettings,
    NarratorSubtitleLanguageMode,
)
from game_optimization_linux.services.narrator_region import (
    EncodedRegionPreview,
    encode_region_preview,
    normalized_region_from_preview,
)

if TYPE_CHECKING:
    from .app_controller import AppController


logger = logging.getLogger(__name__)

# Autostart waits this long after the game's own process is detected.
AUTOSTART_DELAY_SECONDS = 15.0
# A session that lasts this long counts as "on the real game window".
LASTING_SESSION_SECONDS = 90.0
# After "capture session closed": wait, re-check the process, then decide.
CAPTURE_CLOSED_RECHECK_SECONDS = 8.0
# No capture frame this long after the session became active: restart once.
FRAME_WATCHDOG_SECONDS = 10.0
# Consecutive negative process reads (each on a fresh list) before "ended".
ENDED_CONFIRMATIONS = 2
WATCH_REFRESH_SECONDS = 15.0
# How often the autostart watcher logs a liveness heartbeat.
HEARTBEAT_SECONDS = 30.0


class NarratorController:
    def __init__(self, app: AppController) -> None:
        self._app = app
        self._component_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="narrator-components"
        )
        self._component_jobs: dict[str, tuple[str, Future[object]]] = {}
        self._preview_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="narrator-region-preview"
        )
        self._preview_generations: dict[str, int] = {}
        self._preview_jobs: set[Future[object]] = set()
        # game_key -> deadline (monotonic) while waiting for a launched game.
        self._watched: dict[str, Game] = {}
        self._watch_refresh_at = 0.0
        self._seen_since: dict[str, float] = {}
        self._autostart_done: set[str] = set()
        self._last_decision: dict[str, str] = {}
        # Autostart watcher heartbeat: an INFO line roughly every 30 s so the
        # log shows the watcher is alive even when nothing changes.
        self._heartbeat_at = 0.0
        # One automatic capture retry after the portal session is closed
        # (SOURCE_LOST), tracked per game so it fires at most once until the
        # session recovers to a healthy state.
        self._capture_retry_session: set[str] = set()
        # game_key -> monotonic time when "capture session closed" must be
        # re-checked (the game may be switching from a splash window).
        self._capture_closed_pending: dict[str, float] = {}
        # game_key -> (session start time, started automatically).
        self._session_started: dict[str, tuple[float, bool]] = {}
        # Frame watchdog for the current session: (game_key, deadline, frames
        # at arming) once capture is active; one silent restart per game.
        self._frame_watchdog: tuple[str, float, int] | None = None
        self._watchdog_restarted: set[str] = set()
        # "Ended" needs consecutive negative reads on distinct process lists.
        self._negative_reads: dict[str, int] = {}
        self._last_probe: dict[str, object] = {}
        self._clock = time.monotonic

    # -- voice ------------------------------------------------------------------
    def effective_voice_id(self, settings: NarratorGameSettings | None) -> str:
        """The voice really used: the saved one if installed, else the first
        installed voice. A specific voice is never required."""

        tts = self._app._narrator_pipeline.tts
        available = tuple(str(value) for value in getattr(tts, "available_voice_ids", ()))
        saved = settings.voice_id if settings is not None else ""
        if saved and saved in available:
            return saved
        if available:
            return available[0]
        return saved or str(getattr(tts, "default_voice_id", ""))

    def _voice_name(self, voice_id: str) -> str:
        for voice in tuple(getattr(self._app._narrator_pipeline.tts, "voices", ())):
            if isinstance(voice, Mapping):
                if str(voice.get("id", "")) == voice_id:
                    return str(voice.get("name", voice_id))
            elif str(getattr(voice, "voice_id", getattr(voice, "id", ""))) == voice_id:
                return str(getattr(voice, "name", voice_id))
        return voice_id

    def _with_effective_voice(self, settings: NarratorGameSettings) -> NarratorGameSettings:
        voice_id = self.effective_voice_id(settings)
        return replace(settings, voice_id=voice_id) if voice_id != settings.voice_id else settings

    # -- autostart --------------------------------------------------------------
    def game_launched(self, game: Game) -> None:
        """A game was started from GameOpti: look for it right away."""

        game_key = self._game_key(game)
        invalidate = getattr(self._app._narrator_pipeline.activity, "invalidate", None)
        if callable(invalidate):
            invalidate(game_key)
        self._watch_refresh_at = 0.0
        logger.info("Narrator autostart: %s launched from GameOpti", game_key)
        self._app.narratorChanged.emit(game.id)

    def _log_decision(self, game_key: str, message: str) -> None:
        """Log each autostart decision once per change (not on every tick)."""

        if self._last_decision.get(game_key) != message:
            self._last_decision[game_key] = message
            logger.info("Narrator autostart: %s: %s", game_key, message)

    def _refresh_watch(self, now: float) -> None:
        """Games with the Narrator enabled, watched wherever they are started."""

        if now < self._watch_refresh_at:
            return
        self._watch_refresh_at = now + WATCH_REFRESH_SECONDS
        supports = getattr(self._app._narrator_pipeline.activity, "supports", None)
        watched: dict[str, Game] = {}
        for game in tuple(self._app._domain_games.values()):
            game_key = self._game_key(game)
            try:
                enabled = self._app._narrator_settings_repository.load(game_key).enabled
            except Exception:
                continue
            if not enabled:
                continue
            if callable(supports) and supports(game):
                watched[game_key] = game
            else:
                self._log_decision(game_key, "skipped, the game process cannot be observed; start manually")
        self._watched = watched

    def _log_heartbeat(self, now: float, pipeline: object) -> None:
        """INFO liveness line every ~30 s with the current watcher state."""

        if now < self._heartbeat_at:
            return
        self._heartbeat_at = now + HEARTBEAT_SECONDS
        if getattr(pipeline, "active", False):
            logger.info(
                "Narrator autostart heartbeat: session active for %s",
                getattr(pipeline.snapshot, "game_key", "") or "?",
            )
            return
        logger.info(
            "Narrator autostart heartbeat: watching %d game(s): %s",
            len(self._watched),
            ", ".join(sorted(self._watched)) or "none",
        )

    def _read_game_process(self, game_key: str, strict: Any) -> bool | None:
        """True running, False ended (confirmed), None not decided yet.

        A negative read (no match, empty list or unavailable list) counts only
        when it comes from a fresh process list; ENDED_CONFIRMATIONS of them in
        a row are needed before the game counts as ended.
        """

        activity = self._app._narrator_pipeline.activity
        running = strict(game_key)
        if running is True:
            self._negative_reads.pop(game_key, None)
            return True
        probe = getattr(activity, "commands_generation", None)
        fresh = probe is None or self._last_probe.get(game_key) != probe
        self._last_probe[game_key] = probe
        count = self._negative_reads.get(game_key, 0) + (1 if fresh else 0)
        self._negative_reads[game_key] = count
        return False if count >= ENDED_CONFIRMATIONS else None

    def _log_ended(self, game_key: str) -> None:
        activity = self._app._narrator_pipeline.activity
        reason = getattr(activity, "last_reason", {}).get(game_key, "unknown")
        matched = getattr(activity, "last_match", {}).get(game_key, "")
        self._log_decision(
            game_key,
            f"game process ended after {ENDED_CONFIRMATIONS} negative reads "
            f"(reason: {reason}; last matched line: {matched[:200] or 'none'})",
        )

    def _poll_autostart(self) -> None:
        pipeline = self._app._narrator_pipeline
        now = self._clock()
        self._refresh_watch(now)
        self._log_heartbeat(now, pipeline)
        strict = getattr(pipeline.activity, "game_process_running", None)
        if not callable(strict):
            return
        active_key = pipeline.snapshot.game_key if pipeline.active else ""
        if active_key:
            # A running session (manual or automatic) is not restarted after
            # the user stops it while the game still runs.
            self._autostart_done.add(active_key)
        for game_key, game in tuple(self._watched.items()):
            if active_key and game_key != active_key:
                continue
            running = self._read_game_process(game_key, strict)
            if running is None:
                continue          # not confirmed either way; keep the state
            if running is False:
                if game_key in self._seen_since:
                    self._log_ended(game_key)
                self._seen_since.pop(game_key, None)
                self._autostart_done.discard(game_key)
                if game_key == active_key:
                    if self._session_started.get(game_key, (0.0, False))[1]:
                        logger.info(
                            "Narrator autostart: %s: stopping the automatic session, the game ended",
                            game_key,
                        )
                        self.stop()
                    else:
                        self._log_decision(
                            game_key,
                            "session was started manually; keeping it",
                        )
                continue
            if game_key == active_key:
                self._seen_since.setdefault(game_key, now)
                continue
            since = self._seen_since.setdefault(game_key, now)
            if since == now:
                self._log_decision(
                    game_key,
                    f"game process detected, waiting {AUTOSTART_DELAY_SECONDS:.0f} s for its window",
                )
            if game_key in self._autostart_done or now - since < AUTOSTART_DELAY_SECONDS:
                continue
            self._autostart_done.add(game_key)
            try:
                settings = self._app._narrator_settings_repository.load(game_key)
            except Exception as error:
                self._log_decision(game_key, f"error, settings could not be read: {error}")
                continue
            missing = self._missing_requirements(settings) if settings.enabled else ()
            if not settings.enabled or missing:
                self._log_decision(game_key, "skipped, " + (
                    "missing: " + ", ".join(missing) if missing else "disabled"))
                self._app.narratorChanged.emit(game.id)
                continue
            first_grant = not self._has_capture_grant(game_key)
            self._log_decision(game_key, "start" + (" (first window choice)" if first_grant else ""))
            if self.start(game.id, automatic=True):
                self._app._emit_toast(
                    "Narrator started with the game. Choose the game window once; the choice is remembered"
                    if first_grant else "Narrator started with the game",
                    "info",
                )
            else:
                self._log_decision(game_key, "error, the start was rejected")

    def toggle_for_running_game(self) -> bool:
        """Gamepad shortcut: stop the session, or start it for the running game."""

        pipeline = self._app._narrator_pipeline
        sounds = self._app._ui_sound_service
        if pipeline.active:
            logger.info("Narrator shortcut: stop")
            self.stop()
            sounds.play_feedback("close")
            return True
        self._refresh_watch(self._clock())
        for game_key, game in tuple(self._watched.items()):
            if pipeline.activity.is_active(game_key) is True:
                logger.info("Narrator shortcut: start for %s", game_key)
                started = self.start(game.id, automatic=True)
                sounds.play_feedback("open" if started else "error")
                return started
        logger.info("Narrator shortcut: skipped, no running game with the Narrator enabled")
        sounds.play_feedback("error")
        return False

    def choose_window_again(self, game_id: str) -> bool:
        """Forget the saved portal choice for this game only."""

        game = self._app._resolve_game(game_id, show_error=True)
        if game is None:
            return False
        game_key = self._game_key(game)
        capture = self._app._narrator_pipeline.capture
        grants = getattr(capture, "_grants", None)
        try:
            if grants is not None:
                grants.save_token(game_key, "")
        except Exception as error:
            logger.warning("Could not clear the narrator capture choice for %s: %s", game_key, error)
            return False
        getattr(capture, "stale_grants", set()).discard(game_key)
        logger.info("Narrator capture: saved window choice cleared for %s", game_key)
        if self._app._narrator_pipeline.active and self._app._narrator_pipeline.snapshot.game_key == game_key:
            self.stop()
        self._app.narratorChanged.emit(game.id)
        return True

    def _has_capture_grant(self, game_key: str) -> bool:
        grants = getattr(self._app._narrator_pipeline.capture, "_grants", None)
        loader = getattr(grants, "load_token", None)
        try:
            return bool(loader(game_key)) if callable(loader) else True
        except Exception:
            return False

    def components(self) -> list[dict[str, Any]]:
        manager = self._app._narrator_component_manager
        descriptions = {
            "capture.portal-pipewire": "capture_runtime",
            "ocr.english-local": "ocr_model_required",
            "ocr.polish-local": "polish_ocr_model_required",
            "translation.opus-en-pl": "translation_model_required",
            "tts.polish-voice": "polish_voice_required",
            "tts.polish-bass": "polish_voice_required",
            "audio.qt-pcm": "audio_runtime",
        }
        result: list[dict[str, Any]] = []
        for component in manager.list_components():
            values = component.to_dict()
            installing = component.component_id in self._component_jobs
            if installing:
                values["state"] = "installing"
            values.update(
                {
                    "canInstall": (
                        manager.can_install(component.component_id) and not installing
                    ),
                    "canUpdate": bool(
                        component.managed and manager.can_install(component.component_id)
                    ),
                    "canRemove": component.managed,
                    "descriptionCode": descriptions.get(component.component_id, ""),
                }
            )
            result.append(values)
        return result

    def get_global_settings(self) -> dict[str, Any]:
        try:
            settings = self._app._narrator_settings_repository.load_defaults()
        except Exception as error:
            logger.warning("Could not load global narrator settings: %s", error)
            return self._settings_error(str(error))
        result = self._settings_to_qml(None, settings)
        result["global"] = True
        return result

    def save_global_settings(self, values: Mapping[str, Any]) -> bool:
        try:
            repository = self._app._narrator_settings_repository
            current = repository.load_defaults().to_dict()
            aliases = {
                "enabled": "enabled",
                "sourceMode": "source_mode",
                "captureSource": "capture_source",
                "subtitleLanguageMode": "subtitle_language_mode",
                "subtitleAdapterId": "subtitle_adapter_id",
                "ocrProviderId": "ocr_provider_id",
                "translationProviderId": "translation_provider_id",
                "translationProfileId": "translation_profile_id",
                "ttsProviderId": "tts_provider_id",
                "voiceId": "voice_id",
                "volume": "volume",
                "speechRate": "speech_rate",
                "noiseScale": "noise_scale",
                "noiseWScale": "noise_w_scale",
                "captureSamplingHz": "capture_sampling_hz",
                "visualChangeThreshold": "visual_change_threshold",
                "stabilizationMs": "stabilization_ms",
                "ocrMinConfidence": "ocr_min_confidence",
                "duplicateCooldownMs": "duplicate_cooldown_ms",
                "readSpeakerNames": "read_speaker_names",
            }
            for source, target in aliases.items():
                if source in values:
                    current[target] = values[source]
            crop = values.get("subtitleRegion")
            if isinstance(crop, Mapping):
                current["subtitle_region"] = dict(crop)
            current["updated_at"] = datetime.now(UTC).isoformat()
            settings = NarratorGameSettings.from_dict(
                current,
                expected_game_key=str(current["game_key"]),
            )
            repository.save_defaults(settings)
        except Exception as error:
            logger.warning("Could not save global narrator settings: %s", error)
            self._app._emit_toast(
                "Global Narrator settings could not be saved", "error"
            )
            return False
        self._app.narratorChanged.emit("")
        self._app._emit_toast("Global Narrator settings saved", "success")
        return True

    def get_settings(self, game_id: str) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return self._settings_error("Game not found")
        game_key = self._game_key(game)
        try:
            settings = self._app._narrator_settings_repository.load(game_key)
            raw = self._app._narrator_settings_repository.load_overrides(game_key)
        except Exception as error:
            logger.warning("Could not load narrator settings for %s: %s", game.id, error)
            return self._settings_error(str(error), game_id=game.id, game_key=game_key)
        result = self._settings_to_qml(game, settings)
        result["overrideFields"] = [
            field for field in (
                "enabled", "source_mode", "capture_source",
                "subtitle_language_mode", "subtitle_adapter_id",
                "ocr_provider_id", "translation_provider_id",
                "translation_profile_id", "tts_provider_id", "voice_id",
                "volume", "speech_rate", "noise_scale", "noise_w_scale",
                "capture_sampling_hz", "visual_change_threshold",
                "stabilization_ms", "ocr_min_confidence",
                "duplicate_cooldown_ms", "read_speaker_names",
            ) if field in raw
        ]
        result["subtitleRegionInherited"] = False
        return result

    def save_settings(self, game_id: str, values: Mapping[str, Any]) -> bool:
        game = self._app._resolve_game(game_id, show_error=True)
        if game is None:
            return False
        game_key = self._game_key(game)
        try:
            aliases = {
                "enabled": "enabled",
                "sourceMode": "source_mode",
                "captureSource": "capture_source",
                "subtitleLanguageMode": "subtitle_language_mode",
                "subtitleAdapterId": "subtitle_adapter_id",
                "ocrProviderId": "ocr_provider_id",
                "translationProviderId": "translation_provider_id",
                "translationProfileId": "translation_profile_id",
                "ttsProviderId": "tts_provider_id",
                "voiceId": "voice_id",
                "volume": "volume",
                "speechRate": "speech_rate",
                "noiseScale": "noise_scale",
                "noiseWScale": "noise_w_scale",
                "captureSamplingHz": "capture_sampling_hz",
                "visualChangeThreshold": "visual_change_threshold",
                "stabilizationMs": "stabilization_ms",
                "ocrMinConfidence": "ocr_min_confidence",
                "duplicateCooldownMs": "duplicate_cooldown_ms",
                "readSpeakerNames": "read_speaker_names",
            }
            for source, target in aliases.items():
                if source in values:
                    values = dict(values)
                    values[target] = values[source]
            crop = values.get("subtitleRegion")
            if isinstance(crop, Mapping):
                values = dict(values)
                values["subtitle_region"] = dict(crop)
            raw_values = {
                key: value for key, value in dict(values).items()
                if key in set(aliases.values()) or key == "subtitle_region"
            }
            raw_values["updated_at"] = datetime.now(UTC).isoformat()
            repository = self._app._narrator_settings_repository
            previous_region = repository.load(game_key).subtitle_region
            repository.save_overrides(game_key, raw_values)
            settings = repository.load(game_key)
            pipeline = self._app._narrator_pipeline
            if (
                settings.subtitle_region != previous_region
                and pipeline.active
                and pipeline.snapshot.game_key == game_key
            ):
                # Running sessions hold their startup settings. Stop rather than
                # show a saved ROI while OCR continues cropping the old region.
                pipeline.stop()
            select_voice = getattr(
                self._app._narrator_pipeline.tts, "select_voice", None
            )
            effective_voice = self.effective_voice_id(settings)
            if callable(select_voice) and effective_voice:
                select_voice(effective_voice)
        except Exception as error:
            logger.warning("Could not save narrator settings for %s: %s", game.id, error)
            self._app._emit_toast("Narrator settings could not be saved", "error")
            return False
        self._app.narratorChanged.emit(game.id)
        self._app._emit_toast("Narrator settings saved", "success")
        return True

    def clear_overrides(self, game_id: str) -> bool:
        game = self._app._resolve_game(game_id, show_error=True)
        if game is None:
            return False
        try:
            self._app._narrator_settings_repository.clear_overrides(
                self._game_key(game),
                (
                    "enabled", "source_mode", "capture_source",
                    "subtitle_language_mode", "subtitle_adapter_id",
                    "ocr_provider_id", "translation_provider_id",
                    "translation_profile_id", "tts_provider_id", "voice_id",
                    "volume", "speech_rate", "noise_scale", "noise_w_scale",
                    "capture_sampling_hz", "visual_change_threshold",
                    "stabilization_ms", "ocr_min_confidence",
                    "duplicate_cooldown_ms", "read_speaker_names",
                ),
            )
        except Exception as error:
            logger.warning("Could not clear narrator overrides for %s: %s", game.id, error)
            return False
        self._app.narratorChanged.emit(game.id)
        return True

    def session_state(self, game_id: str) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return {
                "status": "idle",
                "canStart": False,
                "reasonCode": "game_not_found",
                "message": "Game not found",
                "missingRequirements": [],
            }
        game_key = self._game_key(game)
        snapshot = self._app._narrator_pipeline.snapshot
        if snapshot.game_key and snapshot.game_key != game_key:
            snapshot_values: dict[str, Any] = {
                "status": "idle",
                "message": "",
                "lastDetectedText": "",
                "lastTranslation": "",
                "lastSpokenText": "",
            }
        else:
            snapshot_values = snapshot.to_dict()
        try:
            settings = self._app._narrator_settings_repository.load(game_key)
        except Exception:
            settings = None
        polish_mode = bool(
            settings is not None
            and settings.subtitle_language_mode
            is NarratorSubtitleLanguageMode.POLISH
        )
        if snapshot_values.get("status") in {"idle", "stopped"}:
            snapshot_values["captureState"] = (
                "stopped"
                if self._app._narrator_pipeline.capture.capabilities().available
                else "unavailable"
            )
            language_available = getattr(
                self._app._narrator_pipeline.ocr, "language_available", None
            )
            ocr_available = bool(
                language_available("pl" if polish_mode else "en")
                if callable(language_available)
                else self._app._narrator_pipeline.ocr.available
            )
            snapshot_values["ocrStatus"] = (
                "ready" if ocr_available else "component_missing"
            )
            snapshot_values["translationStatus"] = (
                "bypassed"
                if polish_mode
                else (
                    "ready"
                    if self._app._narrator_pipeline.translator.available
                    else "component_missing"
                )
            )
            snapshot_values["ttsStatus"] = (
                "ready"
                if self._app._narrator_pipeline.tts.available
                else "component_missing"
            )
            snapshot_values["audioStatus"] = (
                "ready"
                if self._app._narrator_pipeline.audio.available
                else "unavailable"
            )
        missing = list(self._missing_requirements(settings))
        active = self._app._narrator_pipeline.active
        activity_provider = self._app._narrator_pipeline.activity
        activity = activity_provider.is_active(game_key)
        # Unknown (e.g. Flatpak without a host process list) never blocks a
        # manual start; only a positively "not running" game does.
        can_start = not active and not missing and activity is not False
        reason_code = ""
        if active and snapshot.game_key != game_key:
            reason_code = "another_session_active"
        elif missing:
            reason_code = "components_missing"
        elif activity is False:
            reason_code = "game_not_running"
        supports = getattr(activity_provider, "supports", None)
        autostart_supported = bool(callable(supports) and supports(game))
        enabled = bool(settings is not None and settings.enabled)
        running_here = bool(active and snapshot.game_key == game_key)
        if running_here:
            card_state = "running"
        elif snapshot_values.get("status") == "error" and snapshot.game_key == game_key:
            card_state = "error"
        elif not enabled:
            card_state = "disabled"
        elif missing:
            card_state = "missing"
        elif autostart_supported:
            card_state = "waiting_for_game"
        else:
            card_state = "manual"
        voice_id = self.effective_voice_id(settings)
        snapshot_values.update(
            {
                "canStart": can_start,
                "reasonCode": reason_code,
                "missingRequirements": missing,
                "cardState": card_state,
                "gameActivity": (
                    "running" if activity is True
                    else "not_running" if activity is False else "unknown"
                ),
                "autostartSupported": autostart_supported,
                "autostartPending": (
                    game_key in self._seen_since and game_key not in self._autostart_done
                ),
                "captureGrantSaved": self._has_capture_grant(game_key),
                "captureGrantStale": game_key in getattr(
                    self._app._narrator_pipeline.capture, "stale_grants", ()
                ),
                "voiceId": voice_id,
                "voiceName": self._voice_name(voice_id) if voice_id else "",
                "gameId": game.id,
                "gameKey": game_key,
                "subtitleRegion": (
                    settings.subtitle_region.to_dict()
                    if settings is not None
                    else None
                ),
                "lastDetectedAgeSeconds": (
                    max(
                        0.0,
                        time.monotonic()
                        - snapshot.last_detected_at_monotonic,
                    )
                    if snapshot.last_detected_at_monotonic is not None
                    and snapshot.game_key == game_key
                    else None
                ),
            }
        )
        return snapshot_values

    def start(self, game_id: str, *, automatic: bool = False) -> bool:
        game = self._app._resolve_game(game_id, show_error=not automatic)
        if game is None:
            return False
        game_key = self._game_key(game)
        try:
            settings = self._with_effective_voice(
                self._app._narrator_settings_repository.load(game_key)
            )
            missing = self._missing_requirements(settings)
            if missing:
                raise RuntimeError(
                    "Narrator components are unavailable: " + ", ".join(missing)
                )
            self._app._narrator_pipeline.start(settings)
        except Exception as error:
            logger.info("Narrator start rejected for %s: %s", game.id, error)
            self._app._emit_toast(str(error), "warning")
            self._app.narratorChanged.emit(game.id)
            return False
        now = self._clock()
        self._session_started[game_key] = (now, bool(automatic))
        self._frame_watchdog = None
        self._app.narratorChanged.emit(game.id)
        return True

    def _missing_requirements(
        self, settings: NarratorGameSettings | None = None
    ) -> tuple[str, ...]:
        polish_mode = bool(
            settings is not None
            and settings.subtitle_language_mode
            is NarratorSubtitleLanguageMode.POLISH
        )
        selected_voice = self.effective_voice_id(settings)
        voice_components = {
            str(getattr(voice, "voice_id", "")): str(
                getattr(voice, "component_id", "")
            )
            for voice in tuple(
                getattr(self._app._narrator_pipeline.tts, "voices", ())
            )
        }
        required_components = {
            "capture": "capture.portal-pipewire",
            "ocr": "ocr.polish-local" if polish_mode else "ocr.english-local",
            "tts": voice_components.get(selected_voice, "tts.polish-voice"),
            "audio": "audio.qt-pcm",
        }
        if not polish_mode:
            required_components["translation"] = "translation.opus-en-pl"
        component_states = {
            component.component_id: component.state.value
            for component in self._app._narrator_component_manager.list_components()
        }
        missing = {
            kind
            for kind, component_id in required_components.items()
            if component_states.get(component_id) != "available"
        }
        missing.update(
            self._app._narrator_pipeline.missing_requirements(settings)
        )
        if settings is not None:
            available_voices = tuple(
                str(value)
                for value in getattr(
                    self._app._narrator_pipeline.tts,
                    "available_voice_ids",
                    (),
                )
            )
            if selected_voice and selected_voice not in available_voices:
                missing.add("tts")
        return tuple(
            item
            for item in ("capture", "ocr", "translation", "tts", "audio")
            if item in missing
        )

    def stop(self) -> bool:
        snapshot = self._app._narrator_pipeline.stop()
        game = self._game_for_key(snapshot.game_key)
        self._app.narratorChanged.emit(game.id if game is not None else "")
        return True

    def request_region_preview(self, game_id: str) -> bool:
        game = self._app._resolve_game(game_id, show_error=True)
        if game is None:
            return False
        game_key = self._game_key(game)
        try:
            settings = self._app._narrator_settings_repository.load(game_key)
        except Exception as error:
            self._emit_region_preview(
                game.id,
                {"success": False, "state": "error", "error": str(error)},
            )
            return False
        generation = self._preview_generations.get(game.id, 0) + 1
        self._preview_generations[game.id] = generation
        self._emit_region_preview(
            game.id,
            {"success": True, "state": "requesting", "message": ""},
        )

        def frame_received(frame: CaptureFrame) -> None:
            self._app.narratorRegionPreviewStopRequested.emit(game.id, generation)
            future = self._preview_executor.submit(encode_region_preview, frame)
            self._preview_jobs.add(future)

            def encoded(completed: Future[EncodedRegionPreview]) -> None:
                self._preview_jobs.discard(completed)
                if self._preview_generations.get(game.id) != generation:
                    return
                try:
                    preview = completed.result()
                    values = preview.to_dict()
                except Exception as error:
                    logger.exception("Could not prepare Narrator region preview")
                    values = {
                        "success": False,
                        "state": "error",
                        "error": str(error) or error.__class__.__name__,
                    }
                self._emit_region_preview(game.id, values)

            future.add_done_callback(encoded)

        def state_changed(state: CaptureState, message: str) -> None:
            if self._preview_generations.get(game.id) != generation:
                return
            terminal = state in {
                CaptureState.ERROR,
                CaptureState.CANCELLED,
                CaptureState.PERMISSION_DENIED,
                CaptureState.SOURCE_LOST,
                CaptureState.UNAVAILABLE,
            }
            values: dict[str, object] = {
                "success": not terminal,
                "state": state.value,
                "message": str(message),
            }
            if terminal:
                values["error"] = str(message) or "The game frame could not be captured"
                self._app.narratorRegionPreviewStopRequested.emit(
                    game.id, generation
                )
            self._emit_region_preview(game.id, values)

        try:
            self._app._narrator_pipeline.request_preview_frame(
                settings,
                frame_callback=frame_received,
                state_callback=state_changed,
            )
        except Exception as error:
            logger.info("Narrator region preview rejected for %s: %s", game.id, error)
            cancel = getattr(
                self._app._narrator_pipeline, "cancel_preview_frame", None
            )
            if callable(cancel):
                cancel()
            self._emit_region_preview(
                game.id,
                {
                    "success": False,
                    "state": "error",
                    "error": str(error) or error.__class__.__name__,
                },
            )
            return False
        return True

    def cancel_region_preview(self, game_id: str) -> bool:
        normalized = str(game_id)
        self._preview_generations[normalized] = (
            self._preview_generations.get(normalized, 0) + 1
        )
        cancel = getattr(
            self._app._narrator_pipeline, "cancel_preview_frame", None
        )
        if callable(cancel):
            cancel()
        self._emit_region_preview(
            normalized, {"success": True, "state": "cancelled", "message": ""}
        )
        return True

    def finish_region_preview_capture(self, game_id: str, generation: int) -> None:
        if self._preview_generations.get(str(game_id)) == int(generation):
            cancel = getattr(
                self._app._narrator_pipeline, "cancel_preview_frame", None
            )
            if callable(cancel):
                cancel()

    @staticmethod
    def map_region_preview(values: Mapping[str, Any]) -> dict[str, Any]:
        try:
            region = normalized_region_from_preview(
                source_width=int(values.get("sourceWidth", 0)),
                source_height=int(values.get("sourceHeight", 0)),
                viewport_width=float(values.get("viewportWidth", 0.0)),
                viewport_height=float(values.get("viewportHeight", 0.0)),
                selection_x=float(values.get("x", 0.0)),
                selection_y=float(values.get("y", 0.0)),
                selection_width=float(values.get("width", 0.0)),
                selection_height=float(values.get("height", 0.0)),
            )
        except (TypeError, ValueError) as error:
            return {"success": False, "error": str(error)}
        return {"success": True, "region": region.to_dict()}

    def _emit_region_preview(self, game_id: str, values: Mapping[str, object]) -> None:
        self._app.narratorRegionPreviewChanged.emit(game_id, dict(values))

    def install_component(self, component_id: str) -> bool:
        return self._component_action("install", component_id)

    def update_component(self, component_id: str) -> bool:
        return self._component_action("update", component_id)

    def remove_component(self, component_id: str) -> bool:
        return self._component_action("remove", component_id)

    def poll(self) -> None:
        self._poll_component_jobs()
        # Game-exit autostop is decided in _poll_autostart (confirmed "ended",
        # automatic sessions only), not by the pipeline's single probe.
        self._poll_autostart()
        changed: set[str] = set()
        fresh = getattr(getattr(self._app._narrator_pipeline, "capture", None), "fresh_selections", None)
        if fresh:
            fresh.clear()
            # The portal does not say which window was chosen, so ask the
            # user to confirm that it is the game and not GameOpti itself.
            self._app._emit_toast("Check the preview - it should show the game", "info")
        for event in self._app._narrator_pipeline.drain_events():
            game = self._game_for_key(event.game_key)
            if game is not None:
                changed.add(game.id)
            if event.status.value == "error" and event.message:
                if self._retry_after_capture_closed(event, game):
                    continue
                self._app._emit_toast(event.message, "error")
            elif event.status.value in {"listening", "speaking"} and event.game_key:
                # A healthy session clears the one-shot retry guard so a later,
                # independent closure can retry once again.
                self._capture_retry_session.discard(event.game_key)
        for game_id in changed:
            self._app.narratorChanged.emit(game_id)
        now = self._clock()
        self._poll_capture_closed(now)
        self._poll_frame_watchdog(now)
        snapshot = self._app._narrator_pipeline.snapshot
        self._app._ui_sound_service.set_music_ducked(
            snapshot.status.value == "speaking"
            and snapshot.audio_status == "speaking"
        )

    def game_for_key(self, game_key: str) -> Game | None:
        return self._game_for_key(game_key)

    # Portal message emitted when the capture session is closed (the chosen
    # window disappeared or the choice was cancelled). This is the trigger for
    # a single automatic retry, matched before any translation.
    _CAPTURE_CLOSED_MARKER = "capture session closed"

    def _retry_after_capture_closed(self, event: object, game: Game | None) -> bool:
        """Schedule the decision after the portal capture session closes.

        The window may close because the game switches from a splash window to
        its main window, or because the game is exiting (its window disappears
        a few seconds before the process ends). Nothing is decided right away:
        after CAPTURE_CLOSED_RECHECK_SECONDS the process is checked again in
        _poll_capture_closed. Returns True when the closure was taken over (the
        error toast is suppressed).
        """

        message = str(getattr(event, "message", ""))
        if self._CAPTURE_CLOSED_MARKER not in message.casefold():
            return False
        if game is None:
            return False
        game_key = str(getattr(event, "game_key", ""))
        if not game_key:
            return False
        now = self._clock()
        started = self._session_started.get(game_key)
        duration = now - started[0] if started is not None else float("inf")
        self._capture_closed_pending[game_key] = (now + CAPTURE_CLOSED_RECHECK_SECONDS, duration)
        logger.info(
            "Narrator capture: session closed for %s after %.0f s; re-checking the "
            "game process in %.0f s",
            game_key, duration, CAPTURE_CLOSED_RECHECK_SECONDS,
        )
        return True

    def _poll_capture_closed(self, now: float) -> None:
        pipeline = self._app._narrator_pipeline
        for game_key, (due, duration) in tuple(self._capture_closed_pending.items()):
            if now < due:
                continue
            del self._capture_closed_pending[game_key]
            game = self._game_for_key(game_key)
            if game is None or (pipeline.active and pipeline.snapshot.game_key == game_key):
                continue          # gone, or the user already restarted it
            strict = getattr(pipeline.activity, "game_process_running", None)
            running = strict(game_key) if callable(strict) else None
            if (
                running is True
                and duration < LASTING_SESSION_SECONDS
                and game_key not in self._capture_retry_session
            ):
                self._capture_retry_session.add(game_key)
                logger.info(
                    "Narrator capture: %s still running and the session lasted %.0f s "
                    "(< %.0f s, window change); retrying once with the saved window",
                    game_key, duration, LASTING_SESSION_SECONDS,
                )
                automatic = self._session_started.get(game_key, (0.0, True))[1]
                if self.start(game.id, automatic=automatic):
                    self._app._emit_toast("Capture session closed; retrying once", "info")
                continue
            reason = (
                "the game has ended" if running is False
                else "the process list is unavailable" if running is None
                else "already retried once" if game_key in self._capture_retry_session
                else f"the session lasted {duration:.0f} s (game exit)"
            )
            logger.info(
                "Narrator capture: %s: no retry, %s; stopping silently", game_key, reason
            )
            self.stop()

    def _poll_frame_watchdog(self, now: float) -> None:
        """No frame FRAME_WATCHDOG_SECONDS after capture became active: restart
        once, silently, with the saved window token."""

        pipeline = self._app._narrator_pipeline
        snapshot = pipeline.snapshot
        capture = getattr(pipeline, "capture", None)
        frames = getattr(capture, "frames_received", None)
        if (
            not pipeline.active
            or getattr(snapshot, "capture_state", "") != "active"
            or not isinstance(frames, int)
        ):
            self._frame_watchdog = None
            return
        game_key = snapshot.game_key
        watchdog = self._frame_watchdog
        if watchdog is None or watchdog[0] != game_key:
            self._frame_watchdog = (game_key, now + FRAME_WATCHDOG_SECONDS, frames)
            return
        _key, deadline, baseline = watchdog
        if frames > baseline:
            self._watchdog_restarted.discard(game_key)   # frames flow: healthy
            self._frame_watchdog = (game_key, float("inf"), frames)
            return
        if now < deadline:
            return
        self._frame_watchdog = (game_key, float("inf"), frames)
        game = self._game_for_key(game_key)
        if game is None or game_key in self._watchdog_restarted:
            logger.info(
                "Narrator capture: %s: no frames %.0f s after start; already restarted once",
                game_key, FRAME_WATCHDOG_SECONDS,
            )
            return
        self._watchdog_restarted.add(game_key)
        automatic = self._session_started.get(game_key, (0.0, True))[1]
        logger.info(
            "Narrator capture: %s: no frames %.0f s after start; restarting once "
            "silently with the saved window",
            game_key, FRAME_WATCHDOG_SECONDS,
        )
        pipeline.stop()
        self.start(game.id, automatic=automatic)

    def _component_action(self, action: str, component_id: str) -> bool:
        if component_id in self._component_jobs:
            self._app._emit_toast("This narrator component operation is already running", "warning")
            return False
        if self._app._narrator_pipeline.active:
            self._app._emit_toast("Stop the narrator before changing its components", "warning")
            return False
        method = getattr(self._app._narrator_component_manager, action)
        if action in {"install", "update"}:
            future = self._component_executor.submit(method, component_id)
            self._component_jobs[component_id] = (action, future)
            self._app.narratorComponentsChanged.emit()
            return True
        try:
            method(component_id)
        except Exception as error:
            logger.info("Narrator component %s rejected for %s: %s", action, component_id, error)
            self._app._emit_toast(str(error), "warning")
            return False
        self._refresh_component_runtime(component_id)
        self._app.narratorComponentsChanged.emit()
        return True

    def _poll_component_jobs(self) -> None:
        for component_id, (action, future) in tuple(self._component_jobs.items()):
            if not future.done():
                continue
            del self._component_jobs[component_id]
            try:
                future.result()
            except Exception as error:
                logger.warning(
                    "Narrator component %s failed for %s: %s",
                    action,
                    component_id,
                    error,
                )
                self._app._narrator_component_manager.set_runtime_state(
                    component_id, False, str(error)
                )
                self._app._emit_toast(str(error), "error")
            else:
                self._refresh_component_runtime(component_id)
                self._app._emit_toast("Narrator component installed", "success")
            self._app.narratorComponentsChanged.emit()

    def _refresh_component_runtime(self, component_id: str) -> None:
        providers = {
            "ocr.english-local": self._app._narrator_pipeline.ocr,
            "ocr.polish-local": self._app._narrator_pipeline.ocr,
            "translation.opus-en-pl": self._app._narrator_pipeline.translator,
            "tts.polish-voice": self._app._narrator_pipeline.tts,
            "tts.polish-bass": self._app._narrator_pipeline.tts,
            "audio.qt-pcm": self._app._narrator_pipeline.audio,
        }
        provider = providers.get(component_id)
        if provider is None:
            return
        message = getattr(provider, "status_message", "")
        available = bool(provider.available)
        ocr_languages = {
            "ocr.english-local": ("en", "English"),
            "ocr.polish-local": ("pl", "Polish"),
        }
        ocr_language = ocr_languages.get(component_id)
        if ocr_language and hasattr(provider, "language_available"):
            language, language_name = ocr_language
            available = bool(provider.language_available(language))
            message = (
                f"Tesseract {language_name} subtitle OCR is ready"
                if available
                else f"Install the verified {language_name} OCR model"
            )
        voice_ids = {
            "tts.polish-voice": "pl_PL-gosia-medium",
            "tts.polish-bass": "pl_PL-bass-high",
        }
        voice_id = voice_ids.get(component_id)
        if voice_id and hasattr(provider, "voice_available"):
            available = bool(provider.voice_available(voice_id))
            message = (
                f"Piper Polish voice {voice_id} is ready"
                if available
                else f"Install the verified Polish voice {voice_id}"
            )
        manager = self._app._narrator_component_manager
        component = manager.status(component_id)
        if available and component.kind.value in {"ocr", "translation", "tts"}:
            if not component.managed:
                available = False
                message = "Install the verified component through the application"
            else:
                verified, verification_message = manager.verify_installed(component_id)
                if not verified:
                    available = False
                    message = verification_message
        self._app._narrator_component_manager.set_runtime_state(
            component_id, available, str(message)
        )

    def shutdown(self) -> None:
        try:
            cancel = getattr(
                self._app._narrator_pipeline, "cancel_preview_frame", None
            )
            if callable(cancel):
                cancel()
        except Exception:
            logger.exception("Could not stop Narrator region preview capture")
        for future in tuple(self._preview_jobs):
            future.cancel()
        self._preview_jobs.clear()
        self._preview_executor.shutdown(wait=False, cancel_futures=True)
        for _action, future in self._component_jobs.values():
            future.cancel()
        self._component_jobs.clear()
        self._component_executor.shutdown(wait=False, cancel_futures=True)

    def _game_for_key(self, game_key: str) -> Game | None:
        if not game_key:
            return None
        for game in self._app._domain_games.values():
            if self._game_key(game) == game_key:
                return game
        return None

    @staticmethod
    def _game_key(game: Game) -> str:
        if game.steam_app_id:
            return str(game.steam_app_id)
        if game.id.startswith("local-"):
            return game.id
        digest = hashlib.sha256(game.id.encode("utf-8")).hexdigest()[:24]
        return f"local-{digest}"

    def _settings_to_qml(
        self, game: Game | None, settings: NarratorGameSettings
    ) -> dict[str, Any]:
        translator = self._app._narrator_pipeline.translator
        tts = self._app._narrator_pipeline.tts
        profile_ids = (
            tuple(getattr(translator, "profile_ids", ()))
            if translator.available
            else ()
        )
        default_profile = str(getattr(translator, "default_profile_id", ""))
        if not default_profile and profile_ids:
            default_profile = str(profile_ids[0])
        voices: list[dict[str, Any]] = []
        for voice in tuple(getattr(tts, "voices", ())):
            if isinstance(voice, Mapping):
                voice_id = str(voice.get("id", ""))
                name = str(voice.get("name", voice_id))
                component_id = str(voice.get("component_id", ""))
            else:
                voice_id = str(
                    getattr(voice, "voice_id", getattr(voice, "id", ""))
                )
                name = str(getattr(voice, "name", voice_id))
                component_id = str(getattr(voice, "component_id", ""))
            if voice_id:
                installed = bool(
                    getattr(tts, "voice_installed", lambda _voice_id: False)(
                        voice_id
                    )
                )
                available = bool(
                    getattr(tts, "voice_available", lambda _voice_id: False)(
                        voice_id
                    )
                )
                voices.append(
                    {
                        "id": voice_id,
                        "name": name or voice_id,
                        "componentId": component_id,
                        "installed": installed,
                        "available": available,
                    }
                )
        default_voice = str(getattr(tts, "default_voice_id", ""))
        if not default_voice and voices:
            default_voice = voices[0]["id"]
        return {
            "success": True,
            "gameId": game.id if game is not None else "",
            "gameKey": settings.game_key,
            "gameName": game.name if game is not None else "",
            "enabled": settings.enabled,
            "sourceMode": settings.source_mode.value,
            "captureSource": settings.capture_source.value,
            "subtitleLanguageMode": settings.subtitle_language_mode.value,
            "subtitleAdapterId": settings.subtitle_adapter_id,
            "ocrProviderId": (
                settings.ocr_provider_id or self._app._narrator_pipeline.ocr.provider_id
            ),
            "translationProviderId": (
                settings.translation_provider_id or translator.provider_id
            ),
            "translationProfileId": (
                settings.translation_profile_id or default_profile
            ),
            "translationProfiles": [
                {"id": str(profile_id), "name": str(profile_id)}
                for profile_id in profile_ids
            ],
            "ttsProviderId": settings.tts_provider_id or tts.provider_id,
            "voiceId": self.effective_voice_id(settings) or default_voice,
            "voiceName": self._voice_name(self.effective_voice_id(settings) or default_voice),
            "voices": voices,
            "volume": settings.volume,
            "speechRate": settings.speech_rate,
            # None means "use the voice's own value"; QML treats this as unset.
            "noiseScale": settings.noise_scale,
            "noiseWScale": settings.noise_w_scale,
            "subtitleRegion": settings.subtitle_region.to_dict(),
            "captureSamplingHz": settings.capture_sampling_hz,
            "visualChangeThreshold": settings.visual_change_threshold,
            "stabilizationMs": settings.stabilization_ms,
            "ocrMinConfidence": settings.ocr_min_confidence,
            "duplicateCooldownMs": settings.duplicate_cooldown_ms,
            "readSpeakerNames": settings.read_speaker_names,
            "updatedAt": settings.updated_at.isoformat(),
        }

    @staticmethod
    def _settings_error(
        message: str, *, game_id: str = "", game_key: str = ""
    ) -> dict[str, Any]:
        return {
            "success": False,
            "error": message,
            "gameId": game_id,
            "gameKey": game_key,
        }


__all__ = ["NarratorController"]
