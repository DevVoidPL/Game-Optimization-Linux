from __future__ import annotations

from concurrent.futures import Future
from dataclasses import replace
import json
from pathlib import Path
import stat
from threading import Event
import time
from typing import Any, Callable

import pytest
from PySide6.QtCore import QCoreApplication

from game_optimization_linux.controllers import AppController
from game_optimization_linux.models.narrator import (
    CaptureFrame,
    CaptureSourceType,
    CaptureState,
    NarratorComponentKind,
    NarratorGameSettings,
    NarratorSessionStatus,
    NarratorSourceMode,
    NarratorSubtitleLanguageMode,
    NormalizedRect,
    OcrDecisionObservation,
    OcrResult,
    PcmAudio,
    TranslationResult,
)
from game_optimization_linux.services import narrator_persistence as persistence_module
from game_optimization_linux.services.narrator_capture import (
    CaptureCapabilities,
    CaptureRequest,
    CaptureSessionInfo,
    PortalScreenCaptureProvider,
)
from game_optimization_linux.services.narrator_components import (
    NarratorComponentDefinition,
    NarratorComponentManager,
)
from game_optimization_linux.services.narrator_audio import QtNarratorAudioOutput
from game_optimization_linux.services.narrator_persistence import (
    CaptureGrantRepository,
    NarratorSettingsRepository,
    TranslationCache,
)
from game_optimization_linux.services.narrator_pipeline import (
    NarratorPipeline,
    PhraseDeduplicator,
    SubtitleRegionStabilizer,
    UnavailableAudioOutput,
    UnavailableOcrProvider,
    UnavailableTranslationProvider,
    UnavailableTtsProvider,
    crop_frame,
)
from game_optimization_linux.providers import DemoGameProvider
from game_optimization_linux.services import MockTaskService, SettingsStore


_APPLICATION = QCoreApplication.instance() or QCoreApplication([])


class _PortalBackend:
    def __init__(self, capabilities: CaptureCapabilities) -> None:
        self._capabilities = capabilities
        self.requests: list[CaptureRequest] = []
        self.started_callbacks: list[Callable[[CaptureSessionInfo], None]] = []
        self.state_callbacks: list[Callable[[CaptureState, str], None]] = []
        self.stop_calls = 0

    def capabilities(self) -> CaptureCapabilities:
        return self._capabilities

    def start(
        self,
        request: CaptureRequest,
        *,
        frame_callback: Callable[[CaptureFrame], None],
        started_callback: Callable[[CaptureSessionInfo], None],
        state_callback: Callable[[CaptureState, str], None],
    ) -> None:
        del frame_callback
        self.requests.append(request)
        self.started_callbacks.append(started_callback)
        self.state_callbacks.append(state_callback)

    def stop(self) -> None:
        self.stop_calls += 1


class _Capture:
    provider_id = "test-capture"

    def __init__(self) -> None:
        self.requests: list[CaptureRequest] = []
        self.frame_callback: Callable[[CaptureFrame], None] | None = None
        self.state_callback: Callable[[CaptureState, str], None] | None = None
        self.stop_calls = 0

    def capabilities(self) -> CaptureCapabilities:
        return CaptureCapabilities(
            available=True,
            portal_version=6,
            source_types=frozenset({CaptureSourceType.WINDOW}),
            persistence_supported=True,
        )

    def start(
        self,
        request: CaptureRequest,
        *,
        frame_callback: Callable[[CaptureFrame], None],
        state_callback: Callable[[CaptureState, str], None],
    ) -> None:
        self.requests.append(request)
        self.frame_callback = frame_callback
        self.state_callback = state_callback
        state_callback(CaptureState.ACTIVE, "")

    def stop(self) -> None:
        self.stop_calls += 1


class _Activity:
    def __init__(self, active: bool = True) -> None:
        self.active = active

    def is_active(self, game_key: str) -> bool:
        del game_key
        return self.active


class _Ocr:
    provider_id = "test-ocr"
    available = True

    def __init__(self) -> None:
        self.values: list[int] = []

    def recognize(self, frame: CaptureFrame, *, language: str) -> OcrResult:
        assert language == "en"
        value = frame.pixels[0]
        self.values.append(value)
        return OcrResult(
            text=f"phrase {value}",
            confidence=0.95,
            provider_id=self.provider_id,
            elapsed_ms=4.0,
        )


class _Translator:
    provider_id = "test-translator"
    available = True

    def __init__(self) -> None:
        self.values: list[str] = []
        self.cancel_calls = 0

    def translate(
        self,
        text: str,
        *,
        source_language: str,
        target_language: str,
        profile_id: str,
    ) -> TranslationResult:
        assert (source_language, target_language, profile_id) == ("en", "pl", "small")
        self.values.append(text)
        return TranslationResult(
            source_text=text,
            translated_text=f"polski {text}",
            provider_id=self.provider_id,
            elapsed_ms=8.0,
        )

    def cancel(self) -> None:
        self.cancel_calls += 1


class _Tts:
    provider_id = "test-tts"
    available = True

    def __init__(self) -> None:
        self.values: list[str] = []
        self.cancel_calls = 0

    def synthesize(
        self,
        text: str,
        *,
        language: str,
        voice_id: str,
        speech_rate: float,
    ) -> PcmAudio:
        assert (language, voice_id, speech_rate) == ("pl", "voice-pl", 1.0)
        self.values.append(text)
        return PcmAudio(
            samples=b"\0\0",
            sample_rate=24000,
            channels=1,
            provider_id=self.provider_id,
            elapsed_ms=12.0,
        )

    def cancel(self) -> None:
        self.cancel_calls += 1


class _PrewarmingTts(_Tts):
    def __init__(self) -> None:
        super().__init__()
        self.prepared: list[str] = []

    def prepare(self, voice_id: str) -> None:
        self.prepared.append(voice_id)


class _Audio:
    provider_id = "test-audio"
    available = True

    def __init__(self, *, auto_start: bool = True) -> None:
        self.played: list[tuple[int, float]] = []
        # Diagnostics: the phrase handed to the audio layer for each request.
        self.spoken_texts: list[str] = []
        self.started_callbacks: list[Callable[[float], None]] = []
        self.completed_callbacks: list[Callable[[], None]] = []
        self.error_callbacks: list[Callable[[str], None]] = []
        self.stop_calls = 0
        self.auto_start = auto_start

    def play(
        self,
        audio: PcmAudio,
        *,
        volume: float,
        request_id: int,
        started_callback: Callable[[float], None],
        completed_callback: Callable[[], None],
        error_callback: Callable[[str], None],
        text: str = "",
    ) -> None:
        assert audio.samples == b"\0\0"
        self.spoken_texts.append(text)
        self.played.append((request_id, volume))
        self.started_callbacks.append(started_callback)
        self.completed_callbacks.append(completed_callback)
        self.error_callbacks.append(error_callback)
        if self.auto_start:
            started_callback(2.5)

    def stop(self) -> None:
        self.stop_calls += 1


class _ManualExecutor:
    def __init__(self) -> None:
        self.jobs: list[
            tuple[Future[Any], Callable[..., Any], tuple[Any, ...], dict[str, Any]]
        ] = []

    def submit(
        self, function: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> Future[Any]:
        future: Future[Any] = Future()
        self.jobs.append((future, function, args, kwargs))
        return future

    def mark_first_running(self) -> None:
        assert self.jobs
        assert self.jobs[0][0].set_running_or_notify_cancel()

    def run_next(self) -> None:
        self.run_at(0)

    def run_at(self, index: int) -> None:
        future, function, args, kwargs = self.jobs.pop(index)
        if future.cancelled():
            return
        if not future.running() and not future.set_running_or_notify_cancel():
            return
        try:
            result = function(*args, **kwargs)
        except BaseException as error:
            future.set_exception(error)
        else:
            future.set_result(result)


class _AlwaysStable:
    def consider(
        self,
        frame: CaptureFrame,
        *,
        threshold: float,
        stabilization_seconds: float,
    ) -> CaptureFrame:
        del threshold, stabilization_seconds
        return frame


def _settings() -> NarratorGameSettings:
    return NarratorGameSettings(
        game_key="292030",
        enabled=True,
        ocr_provider_id="test-ocr",
        translation_provider_id="test-translator",
        translation_profile_id="small",
        tts_provider_id="test-tts",
        voice_id="voice-pl",
        subtitle_region=NormalizedRect(x=0, y=0, width=1, height=1),
    )


def _frame(
    *, session_id: str, generation: int, value: int, timestamp: float = 1.0
) -> CaptureFrame:
    return CaptureFrame(
        session_id=session_id,
        generation=generation,
        timestamp_monotonic=timestamp,
        width=2,
        height=2,
        stride=2,
        pixel_format="gray8",
        pixels=bytes([value] * 4),
    )


def _pipeline(tmp_path: Path, executor: _ManualExecutor) -> tuple[
    NarratorPipeline, _Capture, _Activity, _Ocr, _Translator, _Tts, _Audio
]:
    capture = _Capture()
    activity = _Activity()
    ocr = _Ocr()
    translator = _Translator()
    tts = _Tts()
    audio = _Audio()
    pipeline = NarratorPipeline(
        capture,
        ocr,
        translator,
        tts,
        audio,
        activity,
        TranslationCache(tmp_path / "translations.sqlite3"),
        executor=executor,  # type: ignore[arg-type]
    )
    return pipeline, capture, activity, ocr, translator, tts, audio


def test_region_preview_uses_one_temporary_portal_frame_and_stops_cleanly(
    tmp_path: Path,
) -> None:
    executor = _ManualExecutor()
    pipeline, capture, _activity, *_providers = _pipeline(tmp_path, executor)
    frames: list[CaptureFrame] = []
    states: list[CaptureState] = []

    pipeline.request_preview_frame(
        _settings(),
        frame_callback=frames.append,
        state_callback=lambda state, _message: states.append(state),
    )

    assert len(capture.requests) == 1
    request = capture.requests[0]
    assert request.game_key == "292030"
    assert request.source_type is CaptureSourceType.WINDOW
    assert request.sampling_hz == 1.0
    assert states == [CaptureState.ACTIVE]
    assert capture.frame_callback is not None
    capture.frame_callback(
        _frame(
            session_id=request.session_id,
            generation=request.generation,
            value=37,
        )
    )

    assert [frame.pixels[0] for frame in frames] == [37]
    assert pipeline.latest_preview_frame("292030") is frames[0]
    pipeline.cancel_preview_frame()
    assert capture.stop_calls == 1


def test_region_preview_reuses_active_narrator_stream_without_second_capture(
    tmp_path: Path,
) -> None:
    executor = _ManualExecutor()
    pipeline, capture, _activity, *_providers = _pipeline(tmp_path, executor)
    snapshot = pipeline.start(_settings())
    full_frame = _frame(
        session_id=snapshot.session_id,
        generation=snapshot.generation,
        value=81,
    )
    pipeline.submit_frame(full_frame)
    frames: list[CaptureFrame] = []

    pipeline.request_preview_frame(
        _settings(),
        frame_callback=frames.append,
        state_callback=lambda _state, _message: None,
    )

    assert frames == [full_frame]
    assert len(capture.requests) == 1
    assert capture.stop_calls == 0
    pipeline.cancel_preview_frame()
    assert capture.stop_calls == 0
    pipeline.stop()


def test_app_controller_region_preview_stops_temporary_capture_after_one_frame(
    tmp_path: Path,
) -> None:
    executor = _ManualExecutor()
    pipeline, capture, _activity, *_providers = _pipeline(tmp_path, executor)
    controller = AppController(
        game_provider=DemoGameProvider(),
        task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        narrator_settings_repository=NarratorSettingsRepository(
            tmp_path / "narrator-games"
        ),
        narrator_component_manager=NarratorComponentManager(
            tmp_path / "components"
        ),
        narrator_pipeline=pipeline,
        auto_refresh=False,
    )
    game_id = controller.games[0]["id"]
    results: list[dict[str, object]] = []
    ready = Event()

    def changed(changed_game_id: str, values: object) -> None:
        if changed_game_id != game_id or not isinstance(values, dict):
            return
        results.append(values)
        if values.get("state") == "ready":
            ready.set()

    controller.narratorRegionPreviewChanged.connect(changed)
    try:
        assert controller.requestNarratorRegionPreview(game_id) is True
        request = capture.requests[-1]
        assert capture.frame_callback is not None
        capture.frame_callback(
            CaptureFrame(
                session_id=request.session_id,
                generation=request.generation,
                timestamp_monotonic=1.0,
                width=4,
                height=2,
                stride=14,
                pixel_format="rgb888",
                pixels=(bytes((20, 40, 60) * 4) + b"\xaa\xbb") * 2,
                source_id="selected-game-window",
            )
        )
        deadline = time.monotonic() + 2.0
        while not ready.is_set() and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            ready.wait(timeout=0.01)
        assert ready.is_set()
        assert capture.stop_calls == 1
        final = results[-1]
        assert final["state"] == "ready"
        assert str(final["imageUrl"]).startswith("data:image/png;base64,")
        assert (final["sourceWidth"], final["sourceHeight"]) == (4, 2)
    finally:
        controller.shutdown()


def test_app_controller_routes_ready_preview_to_native_selector_and_saves_roi(
    tmp_path: Path,
) -> None:
    executor = _ManualExecutor()
    pipeline, capture, _activity, *_providers = _pipeline(tmp_path, executor)
    repository = NarratorSettingsRepository(tmp_path / "narrator-games")
    controller = AppController(
        game_provider=DemoGameProvider(),
        task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        narrator_settings_repository=repository,
        narrator_component_manager=NarratorComponentManager(
            tmp_path / "components"
        ),
        narrator_pipeline=pipeline,
        auto_refresh=False,
    )
    game_id = controller.games[0]["id"]
    initial = {"x": 0.1, "y": 0.6, "width": 0.8, "height": 0.2}
    shown: list[tuple[str, dict[str, object], dict[str, object]]] = []
    ready = Event()

    def show_selector(
        shown_game_id: str,
        preview: dict[str, object],
        initial_region: dict[str, object],
    ) -> None:
        shown.append((shown_game_id, dict(preview), dict(initial_region)))
        ready.set()

    controller._narrator_region_selector.show_selector = show_selector  # type: ignore[method-assign]
    try:
        assert controller.selectNarratorSubtitleRegion(game_id, initial) is True
        request = capture.requests[-1]
        assert capture.frame_callback is not None
        capture.frame_callback(
            CaptureFrame(
                session_id=request.session_id,
                generation=request.generation,
                timestamp_monotonic=1.0,
                width=4,
                height=2,
                stride=12,
                pixel_format="rgb888",
                pixels=bytes((20, 40, 60) * 8),
                source_id="selected-game-window",
            )
        )
        deadline = time.monotonic() + 2.0
        while not ready.is_set() and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            ready.wait(timeout=0.01)

        assert ready.is_set()
        assert shown[0][0] == game_id
        assert shown[0][1]["state"] == "ready"
        assert shown[0][2] == initial
        assert capture.stop_calls == 1

        selected = {"x": 0.2, "y": 0.65, "width": 0.6, "height": 0.18}
        controller._saveNarratorRegionSelection(game_id, selected)
        game_key = str(controller.getNarratorGameSettings(game_id)["gameKey"])
        assert repository.load(game_key).subtitle_region.to_dict() == selected
    finally:
        controller.shutdown()


def test_app_controller_region_preview_reuses_active_rgb888_frame(
    tmp_path: Path,
) -> None:
    executor = _ManualExecutor()
    pipeline, capture, _activity, *_providers = _pipeline(tmp_path, executor)
    controller = AppController(
        game_provider=DemoGameProvider(),
        task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        narrator_settings_repository=NarratorSettingsRepository(
            tmp_path / "narrator-games"
        ),
        narrator_component_manager=NarratorComponentManager(
            tmp_path / "components"
        ),
        narrator_pipeline=pipeline,
        auto_refresh=False,
    )
    game_id = controller.games[0]["id"]
    ready = Event()
    results: list[dict[str, object]] = []

    def changed(changed_game_id: str, values: object) -> None:
        if changed_game_id != game_id or not isinstance(values, dict):
            return
        results.append(values)
        if values.get("state") == "ready":
            ready.set()

    controller.narratorRegionPreviewChanged.connect(changed)
    try:
        game_key = str(controller.getNarratorGameSettings(game_id)["gameKey"])
        snapshot = pipeline.start(replace(_settings(), game_key=game_key))
        frame = CaptureFrame(
            session_id=snapshot.session_id,
            generation=snapshot.generation,
            timestamp_monotonic=1.0,
            width=4,
            height=2,
            stride=14,
            pixel_format="rgb888",
            pixels=(bytes((180, 40, 20) * 4) + b"\x01\x02") * 2,
            source_id="active-game-window",
        )
        pipeline.submit_frame(frame)

        assert controller.requestNarratorRegionPreview(game_id) is True
        deadline = time.monotonic() + 2.0
        while not ready.is_set() and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            ready.wait(timeout=0.01)

        assert ready.is_set()
        assert len(capture.requests) == 1
        assert capture.stop_calls == 0
        # Assert the ready state was REACHED rather than that it was the last
        # element observed: a later "active" update can legitimately arrive after
        # it, which made results[-1] flip intermittently.
        ready_states = [entry for entry in results if entry.get("state") == "ready"]
        assert ready_states, (
            "no ready state observed; saw "
            f"{[entry.get('state') for entry in results]}"
        )
        final = ready_states[-1]
        assert str(final["imageUrl"]).startswith("data:image/png;base64,")
    finally:
        controller.shutdown()


def test_region_preview_conversion_error_is_explicit_and_keeps_saved_roi(
    tmp_path: Path,
) -> None:
    executor = _ManualExecutor()
    pipeline, capture, _activity, *_providers = _pipeline(tmp_path, executor)
    repository = NarratorSettingsRepository(tmp_path / "narrator-games")
    controller = AppController(
        game_provider=DemoGameProvider(),
        task_service=MockTaskService(),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        narrator_settings_repository=repository,
        narrator_component_manager=NarratorComponentManager(
            tmp_path / "components"
        ),
        narrator_pipeline=pipeline,
        auto_refresh=False,
    )
    game_id = controller.games[0]["id"]
    game_key = controller.getNarratorGameSettings(game_id)["gameKey"]
    before = repository.load(str(game_key)).subtitle_region
    failed = Event()
    results: list[dict[str, object]] = []

    def changed(changed_game_id: str, values: object) -> None:
        if changed_game_id != game_id or not isinstance(values, dict):
            return
        results.append(values)
        if values.get("state") == "error":
            failed.set()

    controller.narratorRegionPreviewChanged.connect(changed)
    try:
        assert controller.requestNarratorRegionPreview(game_id) is True
        request = capture.requests[-1]
        assert capture.frame_callback is not None
        capture.frame_callback(
            CaptureFrame(
                session_id=request.session_id,
                generation=request.generation,
                timestamp_monotonic=1.0,
                width=4,
                height=2,
                stride=4,
                pixel_format="unsupported-test-format",
                pixels=b"\0" * 8,
                source_id="selected-game-window",
            )
        )
        deadline = time.monotonic() + 2.0
        while not failed.is_set() and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            failed.wait(timeout=0.01)

        assert failed.is_set()
        assert results[-1]["success"] is False
        assert "unsupported-test-format" in str(results[-1]["error"])
        assert repository.load(str(game_key)).subtitle_region == before
    finally:
        controller.shutdown()


def _confirm_phrase(
    pipeline: NarratorPipeline,
    executor: _ManualExecutor,
    *,
    session_id: str,
    generation: int,
    value: int,
    timestamp: float = 1.0,
) -> None:
    pipeline.submit_frame(
        _frame(
            session_id=session_id,
            generation=generation,
            value=value,
            timestamp=timestamp,
        )
    )
    executor.run_next()
    pipeline.submit_frame(
        _frame(
            session_id=session_id,
            generation=generation,
            value=value,
            timestamp=timestamp + 0.2,
        )
    )
    executor.run_next()


def test_narrator_settings_are_per_game_utf8_atomic_and_survive_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "games"
    repository = NarratorSettingsRepository(root)
    replace_calls: list[tuple[Path, Path]] = []
    real_replace = persistence_module.os.replace

    def replace_spy(source: object, target: object) -> None:
        replace_calls.append((Path(source), Path(target)))
        real_replace(source, target)

    monkeypatch.setattr(persistence_module.os, "replace", replace_spy)
    first = replace(
        NarratorGameSettings.default("292030"),
        enabled=True,
        translation_profile_id="angielski-polish-żółć",
        voice_id="głos-Łucja",
    )
    second = replace(
        NarratorGameSettings.default("242550"), voice_id="inny-głos"
    )

    first_path = repository.save(first)
    repository.save(second)
    restarted = NarratorSettingsRepository(root)

    assert restarted.load("292030") == first
    assert restarted.load("242550") == second
    assert first_path != repository.path("242550")
    assert "głos-Łucja".encode() in first_path.read_bytes()
    assert b"\\u0141" not in first_path.read_bytes()
    assert replace_calls and replace_calls[0][1] == first_path
    assert not list(first_path.parent.glob("*.tmp"))


def test_global_narrator_defaults_are_persistent_and_do_not_replace_game_profiles(
    tmp_path: Path,
) -> None:
    root = tmp_path / "games"
    repository = NarratorSettingsRepository(root)
    global_settings = replace(
        repository.load_defaults(),
        enabled=True,
        subtitle_language_mode="polish",
        voice_id="głos-globalny",
        volume=0.7,
        speech_rate=1.15,
        capture_sampling_hz=7.0,
        visual_change_threshold=0.12,
        stabilization_ms=350,
        ocr_min_confidence=0.75,
        duplicate_cooldown_ms=5000,
    )
    repository.save_defaults(global_settings)

    inherited = repository.load("292030")
    assert inherited.game_key == "292030"
    assert inherited.enabled is True
    assert inherited.voice_id == "głos-globalny"
    assert inherited.volume == 0.7
    assert inherited.speech_rate == 1.15

    per_game = replace(inherited, voice_id="głos-gry", volume=0.4)
    repository.save(per_game)
    repository.save_defaults(replace(global_settings, voice_id="nowy-globalny", volume=0.9))

    restarted = NarratorSettingsRepository(root)
    assert restarted.load("292030") == per_game
    new_game = restarted.load("242550")
    assert new_game.game_key == "242550"
    assert new_game.voice_id == "nowy-globalny"
    assert new_game.volume == 0.9


def test_old_narrator_settings_default_to_english_translation_mode() -> None:
    settings = NarratorGameSettings.from_dict(
        {
            "schema_version": 1,
            "game_key": "292030",
            "enabled": True,
        },
        expected_game_key="292030",
    )

    assert (
        settings.subtitle_language_mode
        is NarratorSubtitleLanguageMode.ENGLISH_TO_POLISH
    )
    assert settings.to_dict()["subtitle_language_mode"] == "english_to_polish"


def test_translation_cache_is_scoped_by_provider_and_profile(tmp_path: Path) -> None:
    cache = TranslationCache(tmp_path / "cache" / "translations.sqlite3")
    cache.put(
        "  The   WITCHER  ",
        "Wiedźmin",
        provider_id="translator-a",
        profile_id="small",
    )

    assert (
        cache.get(
            "the witcher",
            provider_id="translator-a",
            profile_id="small",
        )
        == "Wiedźmin"
    )
    assert (
        cache.get(
            "the witcher",
            provider_id="translator-a",
            profile_id="large",
        )
        is None
    )
    assert stat.S_IMODE(cache.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(cache.path.parent.stat().st_mode) == 0o700
    assert (
        cache.get(
            "the witcher",
            provider_id="translator-b",
            profile_id="small",
        )
        is None
    )


def test_portal_capture_falls_back_to_monitor_and_retries_invalid_restore_token(
    tmp_path: Path,
) -> None:
    grants = CaptureGrantRepository(tmp_path / "capture-grants.json")
    grants.save_token("292030", "old-single-use-token")
    backend = _PortalBackend(
        CaptureCapabilities(
            available=True,
            portal_version=6,
            source_types=frozenset({CaptureSourceType.MONITOR}),
            persistence_supported=True,
        )
    )
    provider = PortalScreenCaptureProvider(backend, grants)
    states: list[tuple[CaptureState, str]] = []

    provider.start(
        CaptureRequest(
            session_id="session-a",
            game_key="292030",
            generation=1,
            source_type=CaptureSourceType.WINDOW,
        ),
        frame_callback=lambda frame: None,
        state_callback=lambda state, message: states.append((state, message)),
    )

    assert backend.requests[0].source_type is CaptureSourceType.MONITOR
    assert backend.requests[0].restore_token == "old-single-use-token"
    backend.state_callbacks[0](CaptureState.RESTORE_FAILED, "expired")
    assert len(backend.requests) == 2
    assert backend.requests[1].restore_token == ""
    assert grants.load_token("292030") == ""
    assert states[-2][0] is CaptureState.SELECTING_SOURCE
    backend.started_callbacks[0](
        CaptureSessionInfo(
            session_id="session-a",
            source_type=CaptureSourceType.MONITOR,
            stream_id="stale-stream",
            restore_token="stale-token",
        )
    )
    assert grants.load_token("292030") == ""
    backend.started_callbacks[-1](
        CaptureSessionInfo(
            session_id="session-a",
            source_type=CaptureSourceType.MONITOR,
            stream_id="pipewire-serial-55",
            restore_token="replacement-token",
        )
    )
    assert grants.load_token("292030") == "replacement-token"
    assert states[-1] == (CaptureState.ACTIVE, "")


def test_portal_capture_reports_second_restore_failure(tmp_path: Path) -> None:
    backend = _PortalBackend(
        CaptureCapabilities(
            available=True,
            portal_version=6,
            source_types=frozenset({CaptureSourceType.WINDOW}),
            persistence_supported=True,
        )
    )
    provider = PortalScreenCaptureProvider(
        backend, CaptureGrantRepository(tmp_path / "capture-grants.json")
    )
    states: list[tuple[CaptureState, str]] = []
    provider.start(
        CaptureRequest(
            session_id="session-b",
            game_key="292030",
            generation=1,
            source_type=CaptureSourceType.WINDOW,
            restore_token="expired-token",
        ),
        frame_callback=lambda _frame: None,
        state_callback=lambda state, message: states.append((state, message)),
    )

    backend.state_callbacks[0](CaptureState.RESTORE_FAILED, "first failure")
    backend.state_callbacks[1](CaptureState.RESTORE_FAILED, "second failure")

    assert states[-1] == (CaptureState.ERROR, "second failure")


def test_crop_uses_negotiated_frame_pixels_and_stride() -> None:
    rows = [bytes(range(row * 10, row * 10 + 10)) + b"\xff\xff" for row in range(8)]
    frame = CaptureFrame(
        session_id="capture-a",
        generation=1,
        timestamp_monotonic=1.0,
        width=10,
        height=8,
        stride=12,
        pixel_format="gray8",
        pixels=b"".join(rows),
    )

    cropped = crop_frame(
        frame, NormalizedRect(x=0.2, y=0.25, width=0.5, height=0.5)
    )

    assert (cropped.width, cropped.height, cropped.stride) == (5, 4, 5)
    assert cropped.pixels == b"".join(
        bytes(range(row * 10 + 2, row * 10 + 7)) for row in range(2, 6)
    )


def test_subtitle_region_stabilization_and_phrase_cooldown() -> None:
    stabilizer = SubtitleRegionStabilizer()
    first = _frame(session_id="s", generation=1, value=10, timestamp=1.0)
    settled = _frame(session_id="s", generation=1, value=10, timestamp=1.3)
    changed = _frame(session_id="s", generation=1, value=100, timestamp=2.0)
    changed_settled = _frame(session_id="s", generation=1, value=100, timestamp=2.3)

    assert stabilizer.consider(first, threshold=0.1, stabilization_seconds=0.2) is None
    assert stabilizer.consider(
        settled, threshold=0.1, stabilization_seconds=0.2
    ) == settled
    assert stabilizer.consider(
        settled, threshold=0.1, stabilization_seconds=0.2
    ) is None
    assert stabilizer.consider(
        changed, threshold=0.1, stabilization_seconds=0.2
    ) is None
    assert stabilizer.consider(
        changed_settled, threshold=0.1, stabilization_seconds=0.2
    ) == changed_settled

    back_to_first = _frame(session_id="s", generation=1, value=10, timestamp=3.0)
    transient = _frame(session_id="s", generation=1, value=100, timestamp=3.1)
    transient_again = _frame(session_id="s", generation=1, value=100, timestamp=3.4)
    assert stabilizer.consider(
        back_to_first, threshold=0.1, stabilization_seconds=0.2
    ) is None
    assert stabilizer.consider(
        transient, threshold=0.1, stabilization_seconds=0.2
    ) is None
    assert stabilizer.consider(
        back_to_first, threshold=0.1, stabilization_seconds=0.2
    ) is None
    assert stabilizer.consider(
        transient_again, threshold=0.1, stabilization_seconds=0.2
    ) is None

    # Normalization still collapses whitespace and ignores case when comparing.
    # The re-acceptance expectations below changed deliberately: this test used to
    # require the same phrase to pass again after a single empty frame or a lapsed
    # cooldown, which is how a subtitle lingering on screen got read twice. One
    # episode is now read once, and only a stable disappearance ends it.
    deduplicator = PhraseDeduplicator()
    assert deduplicator.accept("  Open   the door ", now=1.0, cooldown_seconds=5) == "Open the door"
    assert deduplicator.accept("open the door", now=2.0, cooldown_seconds=5) is None
    assert deduplicator.accept("", now=3.0, cooldown_seconds=5) is None
    # One empty frame is not a disappearance, so the same line stays blocked.
    assert deduplicator.accept("OPEN THE DOOR", now=4.0, cooldown_seconds=5) is None
    # A lapsed cooldown on its own does not re-arm it either.
    assert deduplicator.accept("Open the door", now=10.0, cooldown_seconds=5) is None
    # A stable run of empty observations ends the episode; then it may be read.
    for moment in (11.0, 11.5, 12.0):
        assert deduplicator.accept("", now=moment, cooldown_seconds=5) is None
    assert deduplicator.accept("Open the door", now=13.0, cooldown_seconds=5) == "Open the door"


def test_pipeline_text_consensus_accepts_a_static_subtitle_without_visual_wait(
    tmp_path: Path,
) -> None:
    executor = _ManualExecutor()
    pipeline, _capture, _activity, ocr, translator, tts, audio = _pipeline(
        tmp_path, executor
    )
    snapshot = pipeline.start(_settings())

    for index, timestamp in enumerate((1.0, 1.2)):
        pipeline.submit_frame(
            _frame(
                session_id=snapshot.session_id,
                generation=snapshot.generation,
                value=5,
                timestamp=timestamp,
            )
        )
        assert pipeline.snapshot.last_visual_change_decision == (
            "initial_probe" if index == 0 else "text_confirmation"
        )
        executor.run_next()
    executor.run_next()
    executor.run_next()

    assert ocr.values == [5, 5]
    assert translator.values == ["phrase 5"]
    assert tts.values == ["polski phrase 5"]
    assert len(audio.played) == 1
    assert pipeline.snapshot.last_accepted_ocr_text == "phrase 5"


def test_pipeline_keeps_only_newest_pending_frame_and_discards_stale_work(
    tmp_path: Path,
) -> None:
    executor = _ManualExecutor()
    pipeline, _capture, _activity, ocr, translator, tts, audio = _pipeline(
        tmp_path, executor
    )
    snapshot = pipeline.start(_settings())
    pipeline._stabilizer = _AlwaysStable()  # type: ignore[assignment]

    pipeline.submit_frame(
        _frame(
            session_id=snapshot.session_id,
            generation=snapshot.generation,
            value=1,
            timestamp=1.0,
        )
    )
    pipeline.submit_frame(
        _frame(
            session_id=snapshot.session_id,
            generation=snapshot.generation,
            value=2,
            timestamp=1.01,
        )
    )
    pipeline.submit_frame(
        _frame(
            session_id=snapshot.session_id,
            generation=snapshot.generation,
            value=3,
            timestamp=1.2,
        )
    )
    assert len(executor.jobs) == 1

    executor.run_next()  # OCR 1 is unstable; only OCR 3 is queued.
    executor.run_next()  # First OCR 3 observation is unstable.
    pipeline.submit_frame(
        _frame(
            session_id=snapshot.session_id,
            generation=snapshot.generation,
            value=3,
            timestamp=1.4,
        )
    )
    executor.run_next()  # OCR 3 confirmation.
    executor.run_next()  # Translation 3.
    executor.run_next()  # TTS 3.

    assert ocr.values == [1, 3, 3]
    assert translator.values == ["phrase 3"]
    assert tts.values == ["polski phrase 3"]
    assert audio.played == [(3, pytest.approx(0.85))]
    assert pipeline.snapshot.last_spoken_text == "polski phrase 3"
    assert pipeline.snapshot.dropped_frames == 1
    assert pipeline.snapshot.dropped_capture_sampling == 1
    assert pipeline.snapshot.dropped_capture_coalesced == 0
    assert pipeline.snapshot.unstable_ocr_observations == 2


def test_pipeline_prewarms_selected_tts_without_blocking_start(
    tmp_path: Path,
) -> None:
    executor = _ManualExecutor()
    pipeline, _capture, _activity, _ocr, _translator, _tts, _audio = _pipeline(
        tmp_path, executor
    )
    prewarming_tts = _PrewarmingTts()
    pipeline.tts = prewarming_tts

    snapshot = pipeline.start(_settings())

    assert snapshot.status is NarratorSessionStatus.LISTENING
    assert prewarming_tts.prepared == []
    executor.run_next()
    assert prewarming_tts.prepared == ["voice-pl"]


def test_pipeline_reports_speaking_only_after_audio_really_starts(
    tmp_path: Path,
) -> None:
    executor = _ManualExecutor()
    pipeline, _capture, _activity, _ocr, _translator, _tts, _audio = _pipeline(
        tmp_path, executor
    )
    audio = _Audio(auto_start=False)
    pipeline.audio = audio
    now = [10.0]
    pipeline._clock = lambda: now[0]
    snapshot = pipeline.start(_settings())
    pipeline._stabilizer = _AlwaysStable()  # type: ignore[assignment]
    _confirm_phrase(
        pipeline,
        executor,
        session_id=snapshot.session_id,
        generation=snapshot.generation,
        value=4,
        timestamp=9.0,
    )

    executor.run_next()
    executor.run_next()

    assert pipeline.snapshot.last_translation == "polski phrase 4"
    assert pipeline.snapshot.last_spoken_text == ""
    assert pipeline.snapshot.status is NarratorSessionStatus.TRANSLATING
    assert pipeline.snapshot.audio_status == "waiting"
    assert pipeline.snapshot.translation_status == "ready"
    assert pipeline.snapshot.tts_status == "ready"

    now[0] = 10.4
    audio.started_callbacks[0](3.0)

    assert pipeline.snapshot.status is NarratorSessionStatus.SPEAKING
    assert pipeline.snapshot.last_spoken_text == "polski phrase 4"
    assert pipeline.snapshot.audio_status == "speaking"
    assert pipeline.snapshot.accepted_to_audio_start_ms == pytest.approx(400.0)
    assert pipeline.snapshot.total_capture_to_audio_start_ms == pytest.approx(1200.0)
    assert pipeline.snapshot.confirming_frame_to_audio_start_ms == pytest.approx(
        1200.0
    )
    assert pipeline.snapshot.first_visible_frame_to_audio_start_ms == pytest.approx(
        1400.0
    )
    assert pipeline.snapshot.first_visible_frame_at_monotonic == pytest.approx(9.0)
    assert pipeline.snapshot.confirming_frame_at_monotonic == pytest.approx(9.2)
    assert pipeline.snapshot.accepted_at_monotonic == pytest.approx(10.0)
    assert pipeline.snapshot.tts_started_at_monotonic == pytest.approx(10.0)
    assert pipeline.snapshot.tts_finished_at_monotonic == pytest.approx(10.0)
    assert pipeline.snapshot.playback_started_at_monotonic == pytest.approx(10.4)

    audio.completed_callbacks[0]()

    assert pipeline.snapshot.status is NarratorSessionStatus.LISTENING
    assert pipeline.snapshot.audio_status == "ready"


def test_pipeline_ignores_stale_audio_callbacks_after_stop(tmp_path: Path) -> None:
    executor = _ManualExecutor()
    pipeline, _capture, _activity, _ocr, translator, tts, _audio = _pipeline(
        tmp_path, executor
    )
    audio = _Audio(auto_start=False)
    pipeline.audio = audio
    snapshot = pipeline.start(_settings())
    pipeline._stabilizer = _AlwaysStable()  # type: ignore[assignment]
    _confirm_phrase(
        pipeline,
        executor,
        session_id=snapshot.session_id,
        generation=snapshot.generation,
        value=5,
    )
    executor.run_next()
    executor.run_next()

    pipeline.stop("disabled")
    audio.started_callbacks[0](1.0)
    audio.error_callbacks[0]("late device error")
    audio.completed_callbacks[0]()

    assert pipeline.snapshot.status is NarratorSessionStatus.STOPPED
    assert pipeline.snapshot.message == "disabled"
    assert pipeline.snapshot.last_spoken_text == ""
    assert translator.cancel_calls == 1
    assert tts.cancel_calls == 1


def test_capture_failure_cancels_translation_and_tts_workers(tmp_path: Path) -> None:
    executor = _ManualExecutor()
    pipeline, capture, _activity, _ocr, translator, tts, audio = _pipeline(
        tmp_path, executor
    )
    pipeline.start(_settings())
    assert capture.state_callback is not None

    capture.state_callback(CaptureState.ERROR, "capture stream closed")

    assert pipeline.snapshot.status is NarratorSessionStatus.ERROR
    assert pipeline.snapshot.message == "capture stream closed"
    assert translator.cancel_calls == 1
    assert tts.cancel_calls == 1
    assert capture.stop_calls == 1
    assert audio.stop_calls == 1


def test_pipeline_remains_bounded_while_translation_is_running(tmp_path: Path) -> None:
    executor = _ManualExecutor()
    pipeline, _capture, _activity, ocr, translator, tts, audio = _pipeline(
        tmp_path, executor
    )
    snapshot = pipeline.start(_settings())
    pipeline._stabilizer = _AlwaysStable()  # type: ignore[assignment]

    _confirm_phrase(
        pipeline,
        executor,
        session_id=snapshot.session_id,
        generation=snapshot.generation,
        value=1,
        timestamp=1.0,
    )
    # Translation 1 is queued, but the independent OCR lane can still validate
    # the newest frame without letting raw observations cancel accepted work.
    pipeline.submit_frame(
        _frame(
            session_id=snapshot.session_id,
            generation=snapshot.generation,
            value=2,
            timestamp=1.2,
        )
    )
    pipeline.submit_frame(
        _frame(
            session_id=snapshot.session_id,
            generation=snapshot.generation,
            value=3,
            timestamp=1.4,
        )
    )
    assert len(executor.jobs) == 2

    executor.run_at(1)  # First OCR 3 observation; Translation 1 still survives.
    pipeline.submit_frame(
        _frame(
            session_id=snapshot.session_id,
            generation=snapshot.generation,
            value=3,
            timestamp=1.6,
        )
    )
    executor.run_at(1)  # OCR 3 confirmation cancels queued Translation 1.
    executor.run_next()  # Consume the cancelled Translation 1 future.
    executor.run_next()  # Translation 3.
    executor.run_next()  # TTS 3.

    assert ocr.values == [1, 1, 3, 3]
    assert translator.values == ["phrase 3"]
    assert tts.values == ["polski phrase 3"]
    assert audio.played == [(4, pytest.approx(0.85))]


def test_running_stale_translation_result_never_starts_tts(tmp_path: Path) -> None:
    executor = _ManualExecutor()
    pipeline, _capture, _activity, _ocr, translator, tts, audio = _pipeline(
        tmp_path, executor
    )
    snapshot = pipeline.start(_settings())
    pipeline._stabilizer = _AlwaysStable()  # type: ignore[assignment]

    _confirm_phrase(
        pipeline,
        executor,
        session_id=snapshot.session_id,
        generation=snapshot.generation,
        value=1,
        timestamp=1.0,
    )
    executor.mark_first_running()
    pipeline.submit_frame(
        _frame(
            session_id=snapshot.session_id,
            generation=snapshot.generation,
            value=3,
            timestamp=1.4,
        )
    )
    executor.run_at(1)  # First observation while Translation 1 is running.
    pipeline.submit_frame(
        _frame(
            session_id=snapshot.session_id,
            generation=snapshot.generation,
            value=3,
            timestamp=1.7,
        )
    )
    executor.run_at(1)  # Stable phrase 3 supersedes Translation 1.

    executor.run_next()  # Translation 1 finishes, but its result is discarded.
    executor.run_next()  # Translation 3.
    executor.run_next()  # TTS 3.

    assert translator.values == ["phrase 1", "phrase 3"]
    assert tts.values == ["polski phrase 3"]
    assert audio.played == [(4, pytest.approx(0.85))]
    assert pipeline.snapshot.stale_running_translation_results == 1
    accepted_history = [
        observation
        for observation in pipeline.snapshot.ocr_decision_history
        if observation.accepted
    ]
    assert [observation.decision for observation in accepted_history] == [
        "accepted_but_tts_stale",
        "accepted_tts_submitted",
    ]
    assert accepted_history[0].tts_submitted is False
    assert accepted_history[1].tts_submitted is True


def test_noisy_observation_does_not_cancel_an_accepted_narration_job(
    tmp_path: Path,
) -> None:
    class SequenceOcr(_Ocr):
        def __init__(self) -> None:
            super().__init__()
            self.results = iter(
                (
                    OcrResult("Stay here.", 0.94, self.provider_id),
                    OcrResult("Stay here", 0.92, self.provider_id),
                    OcrResult("2490SDAJCXZNJQ2", 0.93, self.provider_id),
                )
            )

        def recognize(self, frame: CaptureFrame, *, language: str) -> OcrResult:
            self.values.append(frame.pixels[0])
            return next(self.results)

    executor = _ManualExecutor()
    pipeline, _capture, _activity, _ocr, translator, tts, audio = _pipeline(
        tmp_path, executor
    )
    sequence_ocr = SequenceOcr()
    pipeline.ocr = sequence_ocr
    snapshot = pipeline.start(_settings())
    pipeline._stabilizer = _AlwaysStable()  # type: ignore[assignment]

    pipeline.submit_frame(
        _frame(
            session_id=snapshot.session_id,
            generation=snapshot.generation,
            value=1,
            timestamp=1.0,
        )
    )
    executor.run_next()
    pipeline.submit_frame(
        _frame(
            session_id=snapshot.session_id,
            generation=snapshot.generation,
            value=1,
            timestamp=1.2,
        )
    )
    executor.run_next()  # Accepted phrase queues translation.
    pipeline.submit_frame(
        _frame(
            session_id=snapshot.session_id,
            generation=snapshot.generation,
            value=9,
            timestamp=1.4,
        )
    )

    executor.run_next()  # Translation is not discarded by the pending raw frame.
    executor.run_next()  # TTS is not discarded; pending frame then enters OCR.
    executor.run_next()  # Garbage is rejected without creating downstream work.

    assert translator.values == ["Stay here."]
    assert tts.values == ["polski Stay here."]
    assert len(audio.played) == 1
    assert translator.cancel_calls == 0
    assert tts.cancel_calls == 0
    assert pipeline.snapshot.last_ocr_rejection_reason == "alphanumeric_noise"
    assert pipeline.snapshot.last_raw_ocr_text == "2490SDAJCXZNJQ2"
    assert pipeline.snapshot.last_ocr_observation_credible is False
    assert pipeline.snapshot.last_ocr_gate_decision == "rejected_alphanumeric_noise"
    assert pipeline.snapshot.ocr_candidate_observation_count == 0
    assert pipeline.snapshot.ocr_rejection_counts["alphanumeric_noise"] == 1


def test_ocr_decision_history_explains_candidate_acceptance_and_tts(
    tmp_path: Path,
) -> None:
    executor = _ManualExecutor()
    pipeline, _capture, _activity, _ocr, _translator, _tts, _audio = _pipeline(
        tmp_path, executor
    )
    snapshot = pipeline.start(_settings())
    pipeline._stabilizer = _AlwaysStable()  # type: ignore[assignment]

    for timestamp in (1.0, 1.2):
        pipeline.submit_frame(
            _frame(
                session_id=snapshot.session_id,
                generation=snapshot.generation,
                value=7,
                timestamp=timestamp,
            )
        )
        executor.run_next()

    history = pipeline.snapshot.ocr_decision_history
    assert [entry.decision for entry in history] == [
        "candidate_started",
        "accepted_pending_translation",
    ]
    assert history[0].candidate_observation_count == 1
    assert history[1].candidate_observation_count == 2
    assert history[1].accepted_text == "phrase 7"
    assert history[1].tts_submitted is False

    executor.run_next()  # Translation completes and submits TTS.
    accepted = pipeline.snapshot.ocr_decision_history[-1]
    assert accepted.decision == "accepted_tts_submitted"
    assert accepted.tts_submitted is True
    values = pipeline.snapshot.to_dict()["ocrDecisionHistory"]
    assert values[0]["rawText"] == "phrase 7"
    assert values[0]["roiWidth"] == 2
    assert values[0]["backend"] == ""


def test_clean_short_ocr_cluster_reaches_tts_from_one_changed_frame(
    tmp_path: Path,
) -> None:
    class CleanShortOcr(_Ocr):
        def recognize(self, frame: CaptureFrame, *, language: str) -> OcrResult:
            del frame, language
            return OcrResult(
                text="Siema",
                confidence=0.97,
                provider_id=self.provider_id,
                raw_text="Siema",
                filtered_text="Siema",
                token_count=1,
                included_token_count=1,
                line_count=1,
                dropped_token_count=0,
                minimum_token_confidence=0.97,
                geometry_coherent=True,
                clean_short_phrase_evidence=True,
                filter_summary="unchanged",
            )

    executor = _ManualExecutor()
    pipeline, _capture, _activity, _ocr, translator, tts, audio = _pipeline(
        tmp_path, executor
    )
    pipeline.ocr = CleanShortOcr()
    snapshot = pipeline.start(_settings())

    pipeline.submit_frame(
        _frame(
            session_id=snapshot.session_id,
            generation=snapshot.generation,
            value=7,
            timestamp=1.0,
        )
    )
    executor.run_next()  # OCR; strong short evidence submits translation.

    decision = pipeline.snapshot.ocr_decision_history[-1]
    assert decision.accepted_text == "Siema"
    assert decision.candidate_required_observations == 1
    assert decision.candidate_match_kind == "strong_short_evidence"
    assert decision.visual_change_decision == "initial_probe"
    assert decision.clean_short_phrase_evidence is True
    assert decision.filter_summary == "unchanged"

    executor.run_next()  # Translation.
    executor.run_next()  # TTS.

    assert translator.values == ["Siema"]
    assert tts.values == ["polski Siema"]
    assert len(audio.played) == 1


def test_ocr_decision_history_is_bounded_to_twenty(tmp_path: Path) -> None:
    executor = _ManualExecutor()
    pipeline, *_providers = _pipeline(tmp_path, executor)

    for observation_id in range(25):
        pipeline._append_ocr_decision(  # type: ignore[attr-defined]
            OcrDecisionObservation(
                observation_id=observation_id,
                observed_at_monotonic=float(observation_id),
                raw_text=f"raw {observation_id}",
                filtered_text=f"filtered {observation_id}",
                normalized_text=f"normalized {observation_id}",
                confidence=0.9,
                decision="candidate_started",
            )
        )

    history = pipeline.snapshot.ocr_decision_history
    assert len(history) == 20
    assert history[0].observation_id == 5
    assert history[-1].observation_id == 24


def test_empty_transition_frame_does_not_strand_visible_candidate(
    tmp_path: Path,
) -> None:
    class SequenceOcr(_Ocr):
        def __init__(self) -> None:
            super().__init__()
            self.results = iter(
                (
                    OcrResult("Zostań tutaj.", 0.95, self.provider_id),
                    OcrResult("", None, self.provider_id),
                    OcrResult("Zostań tutaj.", 0.94, self.provider_id),
                )
            )

        def recognize(self, frame: CaptureFrame, *, language: str) -> OcrResult:
            del language
            self.values.append(frame.pixels[0])
            return next(self.results)

    executor = _ManualExecutor()
    pipeline, _capture, _activity, _ocr, translator, tts, _audio = _pipeline(
        tmp_path, executor
    )
    sequence_ocr = SequenceOcr()
    pipeline.ocr = sequence_ocr
    snapshot = pipeline.start(_settings())

    for value, timestamp in ((1, 1.0), (0, 1.2), (1, 1.4)):
        pipeline.submit_frame(
            _frame(
                session_id=snapshot.session_id,
                generation=snapshot.generation,
                value=value,
                timestamp=timestamp,
            )
        )
        executor.run_next()

    executor.run_next()  # Translation.
    executor.run_next()  # TTS.

    assert sequence_ocr.values == [1, 0, 1]
    assert translator.values == ["Zostań tutaj."]
    assert tts.values == ["polski Zostań tutaj."]
    history = pipeline.snapshot.ocr_decision_history
    assert [observation.decision for observation in history] == [
        "candidate_started",
        "candidate_retained_after_empty",
        "accepted_tts_submitted",
    ]


def test_disappearance_and_unchanged_subtitle_do_not_repeat_narration(
    tmp_path: Path,
) -> None:
    class SequenceOcr(_Ocr):
        def __init__(self) -> None:
            super().__init__()
            self.results = iter(
                (
                    OcrResult("Run!", 0.94, self.provider_id),
                    OcrResult("Run", 0.92, self.provider_id),
                    OcrResult("", None, self.provider_id),
                    OcrResult("Run!", 0.95, self.provider_id),
                    OcrResult("Run!", 0.95, self.provider_id),
                )
            )

        def recognize(self, frame: CaptureFrame, *, language: str) -> OcrResult:
            self.values.append(frame.pixels[0])
            return next(self.results)

    executor = _ManualExecutor()
    pipeline, _capture, _activity, _ocr, translator, tts, audio = _pipeline(
        tmp_path, executor
    )
    pipeline.ocr = SequenceOcr()
    snapshot = pipeline.start(_settings())
    pipeline._stabilizer = _AlwaysStable()  # type: ignore[assignment]

    for index, timestamp in enumerate((1.0, 1.2)):
        pipeline.submit_frame(
            _frame(
                session_id=snapshot.session_id,
                generation=snapshot.generation,
                value=index + 1,
                timestamp=timestamp,
            )
        )
        executor.run_next()
    executor.run_next()
    executor.run_next()

    pipeline.submit_frame(
        _frame(
            session_id=snapshot.session_id,
            generation=snapshot.generation,
            value=0,
            timestamp=1.4,
        )
    )
    executor.run_next()  # Subtitle disappears.
    for timestamp in (1.6, 1.8):
        pipeline.submit_frame(
            _frame(
                session_id=snapshot.session_id,
                generation=snapshot.generation,
                value=2,
                timestamp=timestamp,
            )
        )
        executor.run_next()

    assert translator.values == ["Run!"]
    assert tts.values == ["polski Run!"]
    assert len(audio.played) == 1
    assert pipeline.snapshot.last_ocr_rejection_reason == "duplicate"
    assert pipeline.snapshot.last_accepted_ocr_text == "Run!"
    assert pipeline.snapshot.last_ocr_observation_credible is True
    assert pipeline.snapshot.last_ocr_gate_decision == "rejected_duplicate"
    assert pipeline.snapshot.ocr_candidate_observation_count == 2
    assert pipeline.snapshot.ocr_candidate_required_observations == 2


def test_pipeline_ignores_inference_completion_after_stop(tmp_path: Path) -> None:
    executor = _ManualExecutor()
    pipeline, _capture, _activity, ocr, translator, tts, audio = _pipeline(
        tmp_path, executor
    )
    snapshot = pipeline.start(_settings())
    pipeline._stabilizer = _AlwaysStable()  # type: ignore[assignment]
    pipeline.submit_frame(
        _frame(session_id=snapshot.session_id, generation=snapshot.generation, value=8)
    )
    executor.mark_first_running()

    pipeline.stop("user stopped")
    executor.run_next()

    assert ocr.values == [8]
    assert translator.values == []
    assert tts.values == []
    assert audio.played == []
    assert pipeline.snapshot.status is NarratorSessionStatus.STOPPED
    assert pipeline.snapshot.message == "user stopped"


def test_pipeline_stops_capture_and_audio_when_game_exits(tmp_path: Path) -> None:
    executor = _ManualExecutor()
    pipeline, capture, activity, _ocr, _translator, _tts, audio = _pipeline(
        tmp_path, executor
    )
    pipeline.start(_settings())

    now = [10.0]
    pipeline._clock = lambda: now[0]
    activity.active = False
    pipeline.poll_game_activity()
    assert pipeline.active
    now[0] = 12.1
    pipeline.poll_game_activity()

    assert pipeline.snapshot.status is NarratorSessionStatus.STOPPED
    assert pipeline.snapshot.message == "The game exited"
    assert capture.stop_calls == 1
    assert audio.stop_calls == 1


def test_pipeline_rejects_unavailable_adapter_mode(tmp_path: Path) -> None:
    executor = _ManualExecutor()
    pipeline, _capture, _activity, _ocr, _translator, _tts, _audio = _pipeline(
        tmp_path, executor
    )

    with pytest.raises(RuntimeError, match="No game subtitle adapter"):
        pipeline.start(replace(_settings(), source_mode=NarratorSourceMode.ADAPTER))


def test_component_manager_removes_only_managed_component_data(tmp_path: Path) -> None:
    definition = NarratorComponentDefinition(
        component_id="ocr.test-model",
        kind=NarratorComponentKind.OCR,
        name="Test OCR model",
        license_id="Apache-2.0",
    )
    manager = NarratorComponentManager(
        tmp_path / "components", definitions=(definition,)
    )
    manifest_path = manager.record_installed(
        definition.component_id,
        version="1.0",
        license_id="Apache-2.0",
        files=[{"path": "model.onnx", "sha256": "0" * 64}],
    )
    payload = manifest_path.parent / "model.onnx"
    payload.write_bytes(b"model")

    assert manager.remove(definition.component_id)
    assert not manifest_path.parent.exists()

    untrusted = manifest_path.parent
    untrusted.mkdir(parents=True)
    (untrusted / "component.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "managed_by": "another-application",
                "component_id": definition.component_id,
            }
        ),
        encoding="utf-8",
    )
    foreign_payload = untrusted / "foreign-model.onnx"
    foreign_payload.write_bytes(b"foreign")

    with pytest.raises(RuntimeError, match="not managed"):
        manager.remove(definition.component_id)
    assert foreign_payload.read_bytes() == b"foreign"


def test_component_status_requires_a_working_runtime_provider(tmp_path: Path) -> None:
    definition = NarratorComponentDefinition(
        component_id="ocr.test-model",
        kind=NarratorComponentKind.OCR,
        name="Test OCR model",
        license_id="Apache-2.0",
    )
    manager = NarratorComponentManager(
        tmp_path / "components", definitions=(definition,)
    )
    manager.record_installed(
        definition.component_id,
        version="1.0",
        license_id="Apache-2.0",
        files=[],
    )

    manager.set_runtime_state(definition.component_id, False, "load failed")
    failed = manager.status(definition.component_id)
    assert failed.state.value == "error"
    assert failed.managed is True

    manager.set_runtime_state(definition.component_id, True, "ready")
    ready = manager.status(definition.component_id)
    assert ready.state.value == "available"
    assert ready.managed is True


def test_qt_audio_output_reports_unsupported_pcm_instead_of_stalling() -> None:
    output = QtNarratorAudioOutput()
    failures: list[str] = []

    output.play(
        PcmAudio(
            samples=b"invalid",
            sample_rate=24000,
            channels=1,
            sample_format="unsupported",
        ),
        volume=0.8,
        request_id=1,
        started_callback=lambda _elapsed: pytest.fail("playback must not start"),
        completed_callback=lambda: pytest.fail("playback must not complete"),
        error_callback=failures.append,
    )
    QCoreApplication.processEvents()

    assert failures == ["Unsupported narrator PCM format: unsupported"]


def test_app_controller_exposes_per_game_narrator_boundary(tmp_path: Path) -> None:
    def build_controller(root: Path) -> AppController:
        pipeline = NarratorPipeline(
            PortalScreenCaptureProvider(
                None, CaptureGrantRepository(root / "capture-grants.json")
            ),
            UnavailableOcrProvider(),
            UnavailableTranslationProvider(),
            UnavailableTtsProvider(),
            UnavailableAudioOutput(),
            _Activity(),
            TranslationCache(root / "translations.sqlite3"),
        )
        return AppController(
            game_provider=DemoGameProvider(),
            task_service=MockTaskService(),
            settings_store=SettingsStore(root / "settings.json"),
            narrator_settings_repository=NarratorSettingsRepository(root / "games"),
            narrator_component_manager=NarratorComponentManager(root / "components"),
            narrator_pipeline=pipeline,
            auto_refresh=False,
        )

    first = build_controller(tmp_path)
    game_id = first.games[0]["id"]
    try:
        assert first.navigate("narrator")
        assert first.currentPage == "narrator"
        settings = first.getNarratorGameSettings(game_id)
        assert settings["success"] is True
        assert first.saveNarratorGameSettings(
            game_id,
            {
                "enabled": True,
                "voiceId": "głos-testowy",
                "subtitleRegion": {
                    "x": 0.1,
                    "y": 0.6,
                    "width": 0.8,
                    "height": 0.3,
                },
            },
        )
        before_cancel = first.getNarratorGameSettings(game_id)["subtitleRegion"]
        assert first.cancelNarratorRegionPreview(game_id) is True
        assert (
            first.getNarratorGameSettings(game_id)["subtitleRegion"]
            == before_cancel
        )
        mapped = first.mapNarratorRegionPreview(
            {
                "sourceWidth": 1920,
                "sourceHeight": 1080,
                "viewportWidth": 1000,
                "viewportHeight": 1000,
                "x": 100,
                "y": 556.25,
                "width": 700,
                "height": 112.5,
            }
        )
        assert mapped["success"] is True
        assert mapped["region"] == pytest.approx(
            {"x": 0.1, "y": 0.6, "width": 0.7, "height": 0.2}
        )
        assert first.saveNarratorGameSettings(
            game_id, {"subtitleRegion": mapped["region"]}
        )
        session = first.getNarratorSessionState(game_id)
        assert session["subtitleRegion"] == pytest.approx(
            {"x": 0.1, "y": 0.6, "width": 0.7, "height": 0.2}
        )
        assert session["canStart"] is False
        assert set(session["missingRequirements"]) == {
            "capture",
            "ocr",
            "translation",
            "tts",
            "audio",
        }
        assert first.saveNarratorGameSettings(
            game_id,
            {"subtitleLanguageMode": "polish"},
        )
        assert (
            first.getNarratorGameSettings(game_id)["subtitleLanguageMode"]
            == "polish"
        )
    finally:
        first.shutdown()

    restored = build_controller(tmp_path)
    try:
        settings = restored.getNarratorGameSettings(game_id)
        assert settings["enabled"] is True
        assert settings["voiceId"] == "głos-testowy"
        assert settings["subtitleLanguageMode"] == "polish"
        assert settings["subtitleRegion"] == {
            "x": 0.1,
            "y": 0.6,
            "width": 0.7,
            "height": 0.2,
        }
    finally:
        restored.shutdown()
