"""Low-latency semantic UI audio and optional Couch Mode menu music."""

from __future__ import annotations

import logging
from pathlib import Path
import time
from typing import Any, Callable

from PySide6.QtCore import (
    QCoreApplication,
    QEvent,
    QObject,
    Property,
    QTimer,
    QUrl,
    Signal,
    Slot,
)


logger = logging.getLogger(__name__)
_DEFAULT_ASSET_ROOT = Path(__file__).resolve().parents[1] / "assets" / "audio"
_EFFECT_FILES = {
    "navigate": "navigate.wav",
    "confirm": "confirm.wav",
    "back": "back.wav",
    "open": "open.wav",
    "close": "close.wav",
    "adjust": "adjust.wav",
    "error": "error.wav",
    "launch": "launch.wav",
}
_ALIASES = {"accept": "confirm", "popup_open": "open", "popup_close": "close"}
_RATE_LIMIT_SECONDS = {"navigate": 0.075, "adjust": 0.12, "error": 0.16}


class UiSoundService(QObject):
    """Own the application's reusable UI-effect and Couch-music players.

    Production lazily creates one ``QSoundEffect`` per semantic event and one
    reusable ``QMediaPlayer`` for the menu loop when Couch Mode first needs
    audio. ``player`` and ``music_player`` remain narrow injection points for
    deterministic tests. Optional audio must never make the application
    unavailable.
    """

    musicStateChanged = Signal()

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        player: Callable[[str], None] | None = None,
        music_player: Callable[..., None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        asset_root: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self._enabled = False  # Legacy desktop interface-sound preference.
        self._menu_sounds_enabled = True
        self._menu_sounds_volume = 0.40
        self._music_enabled = True
        self._music_volume = 0.20
        self._player = player
        self._music_callback = music_player
        self._clock = clock
        self._asset_root = Path(asset_root or _DEFAULT_ASSET_ROOT)
        self._effects: dict[str, Any] = {}
        self._unavailable_effects: set[str] = set()
        self._active: list[tuple[object, object]] = []  # Compatibility cleanup.
        self._last_played: dict[str, float] = {}
        self._audio_error_reported = False
        self._reported_audio_errors: set[str] = set()
        self._music_player: Any | None = None
        self._music_output: Any | None = None
        self._music_started = False
        self._couch_active = False
        self._window_active = False
        self._launch_suspended = False
        self._launch_saw_window_inactive = False
        self._ducked = False
        self._music_gain = 0.0
        self._music_target_gain = 0.0
        self._shutdown = False
        self._initialized = False
        self._fade_timer = QTimer(self)
        self._fade_timer.setInterval(30)
        self._fade_timer.timeout.connect(self._fade_step)

    @Property(bool, notify=musicStateChanged)
    def musicPlaying(self) -> bool:
        return self._music_started and self._music_target_gain > 0.0

    @Property(bool, notify=musicStateChanged)
    def musicDucked(self) -> bool:
        return self._ducked

    @Slot(result=bool)
    def initialize(self) -> bool:
        """Initialize audio lazily when Couch audio is first needed."""

        if self._shutdown:
            return False
        if self._initialized:
            return True
        self._initialized = True
        if self._player is None:
            self._preload_effects()
        return True

    def _preload_effects(self) -> None:
        try:
            from PySide6.QtMultimedia import QMediaDevices

            if QMediaDevices.defaultAudioOutput().isNull():
                self._unavailable_effects.update(_EFFECT_FILES)
                self._report_audio_error(RuntimeError("no audio output device"))
                return
        except Exception as error:
            self._unavailable_effects.update(_EFFECT_FILES)
            self._report_audio_error(error)
            return
        for kind in _EFFECT_FILES:
            self._effect(kind)

    def set_enabled(self, enabled: bool) -> None:
        """Retain the old opt-in desktop sound switch."""

        self._enabled = bool(enabled)
        if not self._enabled:
            self.stop_effects()

    def configure_menu(
        self,
        *,
        sounds_enabled: bool,
        sounds_volume: int | float,
        music_enabled: bool,
        music_volume: int | float,
    ) -> None:
        if self._shutdown:
            return
        self._menu_sounds_enabled = bool(sounds_enabled)
        self._menu_sounds_volume = self._percentage(sounds_volume, 40) / 100.0
        self._music_enabled = bool(music_enabled)
        self._music_volume = self._percentage(music_volume, 20) / 100.0
        for effect in self._effects.values():
            setter = getattr(effect, "setVolume", None)
            if callable(setter):
                setter(self._menu_sounds_volume)
        if not self._menu_sounds_enabled:
            self.stop_effects()
        self._update_music_playback()

    @staticmethod
    def _percentage(value: int | float, default: int) -> int:
        try:
            number = int(round(float(value)))
        except (TypeError, ValueError):
            number = default
        return max(0, min(100, number))

    def play(self, kind: str) -> bool:
        """Legacy desktop API; Couch Mode uses :meth:`play_couch`."""

        if self._shutdown or not self._enabled or not self.initialize():
            return False
        return self._play_effect(kind, rate_limited=False)

    def play_couch(self, kind: str) -> bool:
        if (
            self._shutdown
            or not self._couch_active
            or not self._menu_sounds_enabled
            or not self.initialize()
        ):
            return False
        return self._play_effect(kind, rate_limited=True)

    @staticmethod
    def _status_name(status: object) -> str:
        name = getattr(status, "name", "")
        return str(name or status).rsplit(".", maxsplit=1)[-1]

    def _effect_ready(self, kind: str, effect: object) -> bool:
        status_getter = getattr(effect, "status", None)
        if not callable(status_getter):
            return True
        try:
            status_name = self._status_name(status_getter())
        except (RuntimeError, TypeError) as error:
            self._report_audio_error(error, context=f"effect {kind} status")
            return False
        if status_name.casefold() == "ready":
            return True
        if status_name.casefold() == "error":
            self._unavailable_effects.add(kind)
            self._report_audio_error(
                RuntimeError("QSoundEffect reported Status.Error"),
                context=f"effect {kind} load",
            )
        else:
            logger.debug("Couch audio effect %s is not ready: %s", kind, status_name)
        return False

    def _play_effect(self, kind: str, *, rate_limited: bool) -> bool:
        requested = str(kind).strip().casefold()
        normalized = _ALIASES.get(requested, requested)
        if normalized not in _EFFECT_FILES:
            return False
        now = self._clock()
        minimum_gap = _RATE_LIMIT_SECONDS.get(normalized, 0.0) if rate_limited else 0.0
        if now - self._last_played.get(normalized, -1e9) < minimum_gap:
            return False
        if self._player is not None:
            try:
                self._player(normalized)
            except Exception as error:
                self._report_audio_error(error, context=f"effect {normalized} backend")
                return False
            self._last_played[normalized] = now
            return True
        effect = self._effect(normalized)
        if effect is None or not self._effect_ready(normalized, effect):
            return False
        try:
            is_playing = getattr(effect, "isPlaying", None)
            if normalized in _RATE_LIMIT_SECONDS and callable(is_playing) and is_playing():
                return False
            effect.play()
        except Exception as error:
            self._report_audio_error(error, context=f"effect {normalized} playback")
            return False
        if not self._effect_ready(normalized, effect):
            return False
        self._last_played[normalized] = now
        return True

    def _effect(self, kind: str) -> Any | None:
        existing = self._effects.get(kind)
        if existing is not None:
            return existing
        if kind in self._unavailable_effects:
            return None
        source = self._asset_root / _EFFECT_FILES[kind]
        if not source.is_file():
            self._unavailable_effects.add(kind)
            self._report_audio_error(FileNotFoundError(source))
            return None
        try:
            from PySide6.QtMultimedia import QSoundEffect

            effect = QSoundEffect(self)
            effect.setLoopCount(1)
            effect.setVolume(self._menu_sounds_volume)
            effect.setSource(QUrl.fromLocalFile(str(source)))
            self._effects[kind] = effect
            return effect
        except Exception as error:
            self._unavailable_effects.add(kind)
            self._report_audio_error(error)
            return None

    @Slot(result=bool)
    def playNavigate(self) -> bool:
        return self.play_couch("navigate")

    @Slot(result=bool)
    def playConfirm(self) -> bool:
        return self.play_couch("confirm")

    @Slot(result=bool)
    def playBack(self) -> bool:
        return self.play_couch("back")

    @Slot(result=bool)
    def playOpen(self) -> bool:
        return self.play_couch("open")

    @Slot(result=bool)
    def playClose(self) -> bool:
        return self.play_couch("close")

    @Slot(result=bool)
    def playAdjust(self) -> bool:
        return self.play_couch("adjust")

    @Slot(result=bool)
    def playError(self) -> bool:
        return self.play_couch("error")

    @Slot(result=bool)
    def playLaunch(self) -> bool:
        return self.play_couch("launch")

    @Slot()
    def startMenuMusic(self) -> None:
        self.set_couch_active(True)

    @Slot()
    def stopMenuMusic(self) -> None:
        self.set_couch_active(False)

    @Slot(bool)
    def setMusicDucked(self, ducked: bool) -> None:
        self.set_music_ducked(ducked)

    def set_couch_active(self, active: bool) -> None:
        if self._shutdown:
            return
        normalized = bool(active)
        if normalized == self._couch_active:
            self._update_music_playback()
            return
        self._couch_active = normalized
        if normalized:
            self.initialize()
        else:
            self._launch_suspended = False
            self._launch_saw_window_inactive = False
            self.stop_effects()
        self._update_music_playback()

    def set_window_active(self, active: bool) -> None:
        if self._shutdown:
            return
        normalized = bool(active)
        if self._launch_suspended and not normalized:
            self._launch_saw_window_inactive = True
        returned_from_game = (
            normalized
            and not self._window_active
            and self._launch_suspended
            and self._launch_saw_window_inactive
        )
        self._window_active = normalized
        if returned_from_game:
            self._launch_suspended = False
            self._launch_saw_window_inactive = False
        self._update_music_playback()

    def begin_game_launch(self) -> None:
        if self._shutdown or not self._couch_active:
            return
        self.playLaunch()
        self._launch_suspended = True
        self._launch_saw_window_inactive = False
        self._update_music_playback()

    def cancel_game_launch(self) -> None:
        """Restore menu music after a launch failed or a monitored game returned."""

        if not self._launch_suspended:
            return
        self._launch_suspended = False
        self._launch_saw_window_inactive = False
        self._update_music_playback()

    def set_music_ducked(self, ducked: bool) -> None:
        if self._shutdown:
            return
        normalized = bool(ducked)
        if normalized == self._ducked:
            return
        self._ducked = normalized
        self.musicStateChanged.emit()
        self._update_music_playback()

    def _music_wanted(self) -> bool:
        return (
            not self._shutdown
            and self._couch_active
            and self._window_active
            and self._music_enabled
            and not self._launch_suspended
        )

    def _update_music_playback(self) -> None:
        wanted = self._music_wanted()
        if wanted and not self._music_started:
            if not self._start_music_backend():
                return
        self._music_target_gain = (0.28 if self._ducked else 1.0) if wanted else 0.0
        if self._music_callback is not None:
            if wanted:
                self._set_music_volume(self._music_target_gain)
            elif self._music_started:
                self._set_music_volume(0.0)
                self._pause_music_backend()
            self.musicStateChanged.emit()
            return
        if self._music_started and not self._fade_timer.isActive():
            self._fade_timer.start()
        self.musicStateChanged.emit()

    def _start_music_backend(self) -> bool:
        if self._music_callback is not None:
            self._music_started = True
            self._notify_music("start", self._music_volume)
            return True
        source = self._asset_root / "menu-ambient.wav"
        if not source.is_file():
            self._report_audio_error(FileNotFoundError(source))
            return False
        try:
            if self._music_player is None:
                from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer

                if QMediaDevices.defaultAudioOutput().isNull():
                    raise RuntimeError("no audio output device")
                self._music_output = QAudioOutput(self)
                self._music_output.setVolume(0.0)
                self._music_player = QMediaPlayer(self)
                self._music_player.setAudioOutput(self._music_output)
                self._music_player.errorOccurred.connect(self._on_music_error)
                self._music_player.mediaStatusChanged.connect(
                    self._on_music_status_changed
                )
                self._music_player.setSource(QUrl.fromLocalFile(str(source)))
                self._music_player.setLoops(QMediaPlayer.Loops.Infinite)
            self._music_player.play()
            self._music_started = True
            return True
        except Exception as error:
            self._report_audio_error(error)
            return False

    def _notify_music(self, event: str, volume: float) -> None:
        callback = self._music_callback
        if callback is None:
            return
        try:
            callback(event, volume)
        except TypeError:
            callback(event)

    def _set_music_volume(self, gain: float) -> None:
        self._music_gain = max(0.0, min(1.0, gain))
        volume = self._music_volume * self._music_gain
        if self._music_output is not None:
            self._music_output.setVolume(volume)
        elif self._music_callback is not None:
            self._notify_music("volume", volume)

    def _fade_step(self) -> None:
        difference = self._music_target_gain - self._music_gain
        if abs(difference) <= 0.04:
            self._set_music_volume(self._music_target_gain)
            self._fade_timer.stop()
            if self._music_target_gain <= 0.0 and self._music_started:
                self._pause_music_backend()
            return
        step = 0.10 if difference > 0 else -0.14
        if abs(step) > abs(difference):
            step = difference
        self._set_music_volume(self._music_gain + step)

    def _pause_music_backend(self) -> None:
        if self._music_player is not None:
            try:
                self._music_player.pause()
            except Exception as error:
                self._report_audio_error(error)
        elif self._music_callback is not None:
            self._notify_music("pause", 0.0)
        self._music_started = False

    @Slot(object, str)
    def _on_music_error(self, error: object, message: str = "") -> None:
        detail = str(message or "").strip() or self._status_name(error)
        self._report_audio_error(
            RuntimeError(detail), context="menu music backend"
        )

    @Slot(object)
    def _on_music_status_changed(self, status: object) -> None:
        if self._status_name(status).casefold() != "invalidmedia":
            return
        message = "invalid media"
        if self._music_player is not None:
            error_string = getattr(self._music_player, "errorString", None)
            if callable(error_string):
                message = str(error_string() or message)
        self._report_audio_error(
            RuntimeError(message), context="menu music load"
        )

    def _report_audio_error(
        self, error: object, *, context: str = "backend"
    ) -> None:
        key = f"{context}:{type(error).__name__}:{error}"
        if key in self._reported_audio_errors:
            return
        self._reported_audio_errors.add(key)
        logger.warning(
            "Optional Couch Mode audio is unavailable (%s): %s", context, error
        )
        self._audio_error_reported = True

    def stop_effects(self) -> None:
        for effect in self._effects.values():
            try:
                effect.stop()
            except Exception:
                pass
        for sink, buffer in tuple(self._active):
            self._finish_compat(sink, buffer)

    def _finish_compat(self, sink: object, buffer: object) -> None:
        pair = (sink, buffer)
        if pair in self._active:
            self._active.remove(pair)
        targets = (
            (sink, "stop"),
            (buffer, "close"),
            (sink, "deleteLater"),
            (buffer, "deleteLater"),
        )
        for target, method in targets:
            callback = getattr(target, method, None)
            if callable(callback):
                callback()

    @Slot()
    def shutdown(self) -> None:
        """Release Qt Multimedia objects before QApplication destruction."""

        if self._shutdown:
            return
        self._shutdown = True
        self._music_target_gain = 0.0
        self._fade_timer.stop()
        self._set_music_volume(0.0)
        self.stop_effects()

        player = self._music_player
        output = self._music_output
        if player is not None:
            for signal_name, callback in (
                ("errorOccurred", self._on_music_error),
                ("mediaStatusChanged", self._on_music_status_changed),
            ):
                signal = getattr(player, signal_name, None)
                disconnect = getattr(signal, "disconnect", None)
                if callable(disconnect):
                    try:
                        disconnect(callback)
                    except (RuntimeError, TypeError):
                        pass
            try:
                player.stop()
                player.setAudioOutput(None)
            except (RuntimeError, TypeError) as error:
                self._report_audio_error(error, context="menu music shutdown")
        elif self._music_callback is not None and self._music_started:
            self._notify_music("stop", 0.0)

        try:
            self._fade_timer.timeout.disconnect(self._fade_step)
        except (RuntimeError, TypeError):
            pass

        effects = tuple(self._effects.values())
        self._effects.clear()
        self._unavailable_effects.clear()
        for effect in effects:
            set_source = getattr(effect, "setSource", None)
            if callable(set_source):
                try:
                    set_source(QUrl())
                except (RuntimeError, TypeError):
                    pass
            delete_later = getattr(effect, "deleteLater", None)
            if callable(delete_later):
                delete_later()

        self._music_player = None
        self._music_output = None
        if player is not None:
            player.deleteLater()
        if output is not None:
            try:
                output.setVolume(0.0)
            except (RuntimeError, TypeError):
                pass
            output.deleteLater()

        self._music_started = False
        self._couch_active = False
        self._window_active = False
        self._launch_suspended = False
        self._launch_saw_window_inactive = False
        self._ducked = False
        self._initialized = False
        self.musicStateChanged.emit()

        application = QCoreApplication.instance()
        if application is not None and (effects or player is not None or output is not None):
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            application.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def stop(self) -> None:
        """Backward-compatible alias for :meth:`shutdown`."""

        self.shutdown()


class NoOpUiSoundService(QObject):
    """Semantic audio service for probes that must not initialize multimedia."""

    musicStateChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._sounds_enabled = True
        self._music_enabled = True
        self._couch_active = False
        self._window_active = False
        self._ducked = False
        self._launch_suspended = False
        self._shutdown = False

    @Property(bool, notify=musicStateChanged)
    def musicPlaying(self) -> bool:
        return (
            not self._shutdown
            and self._music_enabled
            and self._couch_active
            and self._window_active
            and not self._launch_suspended
        )

    @Property(bool, notify=musicStateChanged)
    def musicDucked(self) -> bool:
        return self._ducked

    @Slot(result=bool)
    def initialize(self) -> bool:
        return not self._shutdown

    def set_enabled(self, _enabled: bool) -> None:
        return None

    def configure_menu(
        self,
        *,
        sounds_enabled: bool,
        sounds_volume: int | float,
        music_enabled: bool,
        music_volume: int | float,
    ) -> None:
        del sounds_volume, music_volume
        if self._shutdown:
            return
        self._sounds_enabled = bool(sounds_enabled)
        self._music_enabled = bool(music_enabled)
        self.musicStateChanged.emit()

    def play(self, _kind: str) -> bool:
        return False

    def play_couch(self, _kind: str) -> bool:
        return bool(not self._shutdown and self._couch_active and self._sounds_enabled)

    @Slot(result=bool)
    def playNavigate(self) -> bool:
        return self.play_couch("navigate")

    @Slot(result=bool)
    def playConfirm(self) -> bool:
        return self.play_couch("confirm")

    @Slot(result=bool)
    def playBack(self) -> bool:
        return self.play_couch("back")

    @Slot(result=bool)
    def playOpen(self) -> bool:
        return self.play_couch("open")

    @Slot(result=bool)
    def playClose(self) -> bool:
        return self.play_couch("close")

    @Slot(result=bool)
    def playAdjust(self) -> bool:
        return self.play_couch("adjust")

    @Slot(result=bool)
    def playError(self) -> bool:
        return self.play_couch("error")

    @Slot(result=bool)
    def playLaunch(self) -> bool:
        return self.play_couch("launch")

    @Slot()
    def startMenuMusic(self) -> None:
        self.set_couch_active(True)

    @Slot()
    def stopMenuMusic(self) -> None:
        self.set_couch_active(False)

    @Slot(bool)
    def setMusicDucked(self, ducked: bool) -> None:
        self.set_music_ducked(ducked)

    def set_couch_active(self, active: bool) -> None:
        if not self._shutdown:
            self._couch_active = bool(active)
            if not self._couch_active:
                self._launch_suspended = False
            self.musicStateChanged.emit()

    def set_window_active(self, active: bool) -> None:
        if not self._shutdown:
            was_active = self._window_active
            self._window_active = bool(active)
            if self._window_active and not was_active and self._launch_suspended:
                self._launch_suspended = False
            self.musicStateChanged.emit()

    def begin_game_launch(self) -> None:
        if not self._shutdown and self._couch_active:
            self._launch_suspended = True
            self.musicStateChanged.emit()

    def cancel_game_launch(self) -> None:
        if not self._shutdown and self._launch_suspended:
            self._launch_suspended = False
            self.musicStateChanged.emit()

    def set_music_ducked(self, ducked: bool) -> None:
        if not self._shutdown and self._ducked != bool(ducked):
            self._ducked = bool(ducked)
            self.musicStateChanged.emit()

    def stop_effects(self) -> None:
        return None

    @Slot()
    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        self._couch_active = False
        self._window_active = False
        self._launch_suspended = False
        self._ducked = False
        self.musicStateChanged.emit()

    def stop(self) -> None:
        self.shutdown()


__all__ = ["NoOpUiSoundService", "UiSoundService"]
