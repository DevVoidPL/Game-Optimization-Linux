"""QML-facing narrator settings and session boundary."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
import hashlib
import logging
import time
from typing import TYPE_CHECKING, Any, Mapping

from game_optimization_linux.models import Game
from game_optimization_linux.models.narrator import NarratorGameSettings

if TYPE_CHECKING:
    from .app_controller import AppController


logger = logging.getLogger(__name__)


class NarratorController:
    def __init__(self, app: AppController) -> None:
        self._app = app
        self._component_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="narrator-components"
        )
        self._component_jobs: dict[str, tuple[str, Future[object]]] = {}

    def components(self) -> list[dict[str, Any]]:
        manager = self._app._narrator_component_manager
        descriptions = {
            "capture.portal-pipewire": "capture_runtime",
            "ocr.english-local": "ocr_model_required",
            "translation.opus-en-pl": "translation_model_required",
            "tts.polish-voice": "polish_voice_required",
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

    def get_settings(self, game_id: str) -> dict[str, Any]:
        game = self._app._resolve_game(game_id, show_error=False)
        if game is None:
            return self._settings_error("Game not found")
        game_key = self._game_key(game)
        try:
            settings = self._app._narrator_settings_repository.load(game_key)
        except Exception as error:
            logger.warning("Could not load narrator settings for %s: %s", game.id, error)
            return self._settings_error(str(error), game_id=game.id, game_key=game_key)
        return self._settings_to_qml(game, settings)

    def save_settings(self, game_id: str, values: Mapping[str, Any]) -> bool:
        game = self._app._resolve_game(game_id, show_error=True)
        if game is None:
            return False
        game_key = self._game_key(game)
        try:
            current = self._app._narrator_settings_repository.load(game_key).to_dict()
            aliases = {
                "enabled": "enabled",
                "sourceMode": "source_mode",
                "captureSource": "capture_source",
                "subtitleAdapterId": "subtitle_adapter_id",
                "ocrProviderId": "ocr_provider_id",
                "translationProviderId": "translation_provider_id",
                "translationProfileId": "translation_profile_id",
                "ttsProviderId": "tts_provider_id",
                "voiceId": "voice_id",
                "volume": "volume",
                "speechRate": "speech_rate",
                "captureSamplingHz": "capture_sampling_hz",
                "visualChangeThreshold": "visual_change_threshold",
                "stabilizationMs": "stabilization_ms",
                "ocrMinConfidence": "ocr_min_confidence",
                "duplicateCooldownMs": "duplicate_cooldown_ms",
            }
            for source, target in aliases.items():
                if source in values:
                    current[target] = values[source]
            crop = values.get("subtitleRegion")
            if isinstance(crop, Mapping):
                current["subtitle_region"] = dict(crop)
            current["updated_at"] = datetime.now(UTC).isoformat()
            settings = NarratorGameSettings.from_dict(
                current, expected_game_key=game_key
            )
            self._app._narrator_settings_repository.save(settings)
        except Exception as error:
            logger.warning("Could not save narrator settings for %s: %s", game.id, error)
            self._app._emit_toast("Narrator settings could not be saved", "error")
            return False
        self._app.narratorChanged.emit(game.id)
        self._app._emit_toast("Narrator settings saved", "success")
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
        if snapshot_values.get("status") in {"idle", "stopped"}:
            snapshot_values["captureState"] = (
                "stopped"
                if self._app._narrator_pipeline.capture.capabilities().available
                else "unavailable"
            )
            snapshot_values["ocrStatus"] = (
                "ready"
                if self._app._narrator_pipeline.ocr.available
                else "component_missing"
            )
            snapshot_values["translationStatus"] = (
                "ready"
                if self._app._narrator_pipeline.translator.available
                else "component_missing"
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
        missing = list(self._missing_requirements())
        active = self._app._narrator_pipeline.active
        activity = self._app._narrator_pipeline.activity.is_active(game_key)
        can_start = not active and not missing and activity is True
        reason_code = ""
        if active and snapshot.game_key != game_key:
            reason_code = "another_session_active"
        elif missing:
            reason_code = "components_missing"
        elif activity is not True:
            reason_code = "game_not_running"
        snapshot_values.update(
            {
                "canStart": can_start,
                "reasonCode": reason_code,
                "missingRequirements": missing,
                "gameId": game.id,
                "gameKey": game_key,
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

    def start(self, game_id: str) -> bool:
        game = self._app._resolve_game(game_id, show_error=True)
        if game is None:
            return False
        game_key = self._game_key(game)
        try:
            missing = self._missing_requirements()
            if missing:
                raise RuntimeError(
                    "Narrator components are unavailable: " + ", ".join(missing)
                )
            settings = self._app._narrator_settings_repository.load(game_key)
            self._app._narrator_pipeline.start(settings)
        except Exception as error:
            logger.info("Narrator start rejected for %s: %s", game.id, error)
            self._app._emit_toast(str(error), "warning")
            self._app.narratorChanged.emit(game.id)
            return False
        self._app.narratorChanged.emit(game.id)
        return True

    def _missing_requirements(self) -> tuple[str, ...]:
        required = {"capture", "ocr", "translation", "tts", "audio"}
        available = {
            component.kind.value
            for component in self._app._narrator_component_manager.list_components()
            if component.state.value == "available"
        }
        missing = required - available
        missing.update(self._app._narrator_pipeline.missing_requirements())
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

    def install_component(self, component_id: str) -> bool:
        return self._component_action("install", component_id)

    def update_component(self, component_id: str) -> bool:
        return self._component_action("update", component_id)

    def remove_component(self, component_id: str) -> bool:
        return self._component_action("remove", component_id)

    def poll(self) -> None:
        self._poll_component_jobs()
        self._app._narrator_pipeline.poll_game_activity()
        changed: set[str] = set()
        for event in self._app._narrator_pipeline.drain_events():
            game = self._game_for_key(event.game_key)
            if game is not None:
                changed.add(game.id)
            if event.status.value == "error" and event.message:
                self._app._emit_toast(event.message, "error")
        for game_id in changed:
            self._app.narratorChanged.emit(game_id)

    def game_for_key(self, game_key: str) -> Game | None:
        return self._game_for_key(game_key)

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
            "translation.opus-en-pl": self._app._narrator_pipeline.translator,
            "tts.polish-voice": self._app._narrator_pipeline.tts,
            "audio.qt-pcm": self._app._narrator_pipeline.audio,
        }
        provider = providers.get(component_id)
        if provider is None:
            return
        message = getattr(provider, "status_message", "")
        available = bool(provider.available)
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
        self, game: Game, settings: NarratorGameSettings
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
        voices: list[dict[str, str]] = []
        for voice in tuple(getattr(tts, "voices", ())) if tts.available else ():
            if isinstance(voice, Mapping):
                voice_id = str(voice.get("id", ""))
                name = str(voice.get("name", voice_id))
            else:
                voice_id = str(
                    getattr(voice, "voice_id", getattr(voice, "id", ""))
                )
                name = str(getattr(voice, "name", voice_id))
            if voice_id:
                voices.append({"id": voice_id, "name": name or voice_id})
        default_voice = str(getattr(tts, "default_voice_id", ""))
        if not default_voice and voices:
            default_voice = voices[0]["id"]
        return {
            "success": True,
            "gameId": game.id,
            "gameKey": settings.game_key,
            "gameName": game.name,
            "enabled": settings.enabled,
            "sourceMode": settings.source_mode.value,
            "captureSource": settings.capture_source.value,
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
            "voiceId": settings.voice_id or default_voice,
            "voices": voices,
            "volume": settings.volume,
            "speechRate": settings.speech_rate,
            "subtitleRegion": settings.subtitle_region.to_dict(),
            "captureSamplingHz": settings.capture_sampling_hz,
            "visualChangeThreshold": settings.visual_change_threshold,
            "stabilizationMs": settings.stabilization_ms,
            "ocrMinConfidence": settings.ocr_min_confidence,
            "duplicateCooldownMs": settings.duplicate_cooldown_ms,
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
