"""Screen-capture boundary for narrator subtitle acquisition."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Protocol

from game_optimization_linux.models.narrator import (
    CaptureFrame,
    CaptureSourceType,
    CaptureState,
)

from .narrator_persistence import CaptureGrantRepository


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CaptureCapabilities:
    available: bool
    portal_version: int = 0
    source_types: frozenset[CaptureSourceType] = frozenset()
    persistence_supported: bool = False
    message: str = ""


@dataclass(frozen=True, slots=True)
class CaptureRequest:
    session_id: str
    game_key: str
    generation: int
    source_type: CaptureSourceType
    restore_token: str = ""
    cursor_visible: bool = False
    sampling_hz: float = 6.0


@dataclass(frozen=True, slots=True)
class CaptureSessionInfo:
    session_id: str
    source_type: CaptureSourceType
    stream_id: str
    restore_token: str = ""


FrameCallback = Callable[[CaptureFrame], None]
StateCallback = Callable[[CaptureState, str], None]


class ScreenCaptureProvider(Protocol):
    provider_id: str

    def capabilities(self) -> CaptureCapabilities: ...

    def start(
        self,
        request: CaptureRequest,
        *,
        frame_callback: FrameCallback,
        state_callback: StateCallback,
    ) -> None: ...

    def stop(self) -> None: ...


class PortalScreenCastBackend(Protocol):
    """Portal and PipeWire transport implemented outside the pipeline."""

    def capabilities(self) -> CaptureCapabilities: ...

    def start(
        self,
        request: CaptureRequest,
        *,
        frame_callback: FrameCallback,
        started_callback: Callable[[CaptureSessionInfo], None],
        state_callback: StateCallback,
    ) -> None: ...

    def stop(self) -> None: ...

    def close(self) -> None: ...


class PortalScreenCaptureProvider:
    """Coordinate portal permission persistence without coupling it to OCR.

    The concrete backend owns the asynchronous ScreenCast request sequence and
    the PipeWire buffers. This provider owns source fallback and single-use
    restore-token replacement.
    """

    provider_id = "portal-pipewire"

    def __init__(
        self,
        backend: PortalScreenCastBackend | None,
        grants: CaptureGrantRepository | None = None,
    ) -> None:
        self._backend = backend
        self._grants = grants or CaptureGrantRepository()
        self._request: CaptureRequest | None = None
        self._frame_callback: FrameCallback | None = None
        self._state_callback: StateCallback | None = None
        self._restore_retry_used = False
        self._attempt = 0

    def capabilities(self) -> CaptureCapabilities:
        if self._backend is None:
            return CaptureCapabilities(
                available=False,
                message=(
                    "The portal capture transport is not installed in this build"
                ),
            )
        return self._backend.capabilities()

    def start(
        self,
        request: CaptureRequest,
        *,
        frame_callback: FrameCallback,
        state_callback: StateCallback,
    ) -> None:
        capabilities = self.capabilities()
        if not capabilities.available or self._backend is None:
            state_callback(CaptureState.UNAVAILABLE, capabilities.message)
            return
        if request.source_type not in capabilities.source_types:
            if CaptureSourceType.MONITOR not in capabilities.source_types:
                state_callback(
                    CaptureState.UNAVAILABLE,
                    "The portal does not offer the requested capture source",
                )
                return
            request = CaptureRequest(
                session_id=request.session_id,
                game_key=request.game_key,
                generation=request.generation,
                source_type=CaptureSourceType.MONITOR,
                restore_token=request.restore_token,
                cursor_visible=request.cursor_visible,
                sampling_hz=request.sampling_hz,
            )
        restore_token = (
            request.restore_token
            or (
                self._grants.load_token(request.game_key)
                if capabilities.persistence_supported
                else ""
            )
        )
        self._request = CaptureRequest(
            session_id=request.session_id,
            game_key=request.game_key,
            generation=request.generation,
            source_type=request.source_type,
            restore_token=restore_token,
            cursor_visible=request.cursor_visible,
            sampling_hz=request.sampling_hz,
        )
        self._frame_callback = frame_callback
        self._state_callback = state_callback
        self._restore_retry_used = False
        self._begin(self._request)

    def stop(self) -> None:
        backend = self._backend
        if backend is not None:
            backend.stop()
        self._request = None
        self._frame_callback = None
        self._state_callback = None
        self._restore_retry_used = False
        self._attempt += 1

    def close(self) -> None:
        backend = self._backend
        self._request = None
        self._frame_callback = None
        self._state_callback = None
        self._restore_retry_used = False
        self._attempt += 1
        if backend is None:
            return
        close = getattr(backend, "close", None)
        if callable(close):
            close()
        else:
            backend.stop()

    def _begin(self, request: CaptureRequest) -> None:
        backend = self._backend
        if backend is None or self._frame_callback is None or self._state_callback is None:
            return
        self._attempt += 1
        attempt = self._attempt
        self._state_callback(CaptureState.PERMISSION_REQUIRED, "")
        backend.start(
            request,
            frame_callback=lambda frame: self._on_frame(frame, attempt),
            started_callback=lambda info: self._on_started(info, attempt),
            state_callback=lambda state, message: self._on_state(
                state, message, attempt
            ),
        )

    def _on_started(self, info: CaptureSessionInfo, attempt: int) -> None:
        request = self._request
        callback = self._state_callback
        if (
            request is None
            or callback is None
            or attempt != self._attempt
            or info.session_id != request.session_id
        ):
            return
        if info.restore_token:
            try:
                self._grants.save_token(request.game_key, info.restore_token)
            except Exception as error:
                logger.warning("Could not save narrator portal restore token: %s", error)
        callback(CaptureState.ACTIVE, "")

    def _on_state(self, state: CaptureState, message: str, attempt: int) -> None:
        request = self._request
        callback = self._state_callback
        if request is None or callback is None or attempt != self._attempt:
            return
        if (
            state is CaptureState.RESTORE_FAILED
            and request.restore_token
            and not self._restore_retry_used
        ):
            self._restore_retry_used = True
            try:
                self._grants.save_token(request.game_key, "")
            except Exception as error:
                logger.warning("Could not clear narrator portal restore token: %s", error)
            if self._backend is not None:
                self._backend.stop()
            retry = CaptureRequest(
                session_id=request.session_id,
                game_key=request.game_key,
                generation=request.generation,
                source_type=request.source_type,
                restore_token="",
                cursor_visible=request.cursor_visible,
                sampling_hz=request.sampling_hz,
            )
            self._request = retry
            callback(
                CaptureState.SELECTING_SOURCE,
                "The saved capture permission could not be restored; select a source again",
            )
            self._begin(retry)
            return
        if state is CaptureState.RESTORE_FAILED:
            callback(
                CaptureState.ERROR,
                message or "The saved capture permission could not be restored",
            )
            return
        callback(state, message)

    def _on_frame(self, frame: CaptureFrame, attempt: int) -> None:
        request = self._request
        callback = self._frame_callback
        if (
            request is None
            or callback is None
            or attempt != self._attempt
            or frame.session_id != request.session_id
            or frame.generation != request.generation
        ):
            return
        callback(frame)


__all__ = [
    "CaptureCapabilities",
    "CaptureRequest",
    "CaptureSessionInfo",
    "PortalScreenCaptureProvider",
    "PortalScreenCastBackend",
    "ScreenCaptureProvider",
]
