"""Typed xdg-desktop-portal ScreenCast transport."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from concurrent.futures import Future as ConcurrentFuture
from dataclasses import dataclass
import logging
import os
import re
import threading
from typing import Any, Coroutine
from uuid import uuid4

from dbus_next import BusType, Message, MessageType, Variant
from dbus_next.aio import MessageBus
from PySide6.QtCore import QObject, Qt, Signal, Slot

from game_optimization_linux.models.narrator import CaptureSourceType, CaptureState

from .narrator_capture import (
    CaptureCapabilities,
    CaptureRequest,
    CaptureSessionInfo,
    FrameCallback,
    StateCallback,
)
from .narrator_gstreamer import GStreamerPipeWireTransport


logger = logging.getLogger(__name__)

_PORTAL_SERVICE = "org.freedesktop.portal.Desktop"
_PORTAL_PATH = "/org/freedesktop/portal/desktop"
_SCREENCAST_INTERFACE = "org.freedesktop.portal.ScreenCast"
_REQUEST_INTERFACE = "org.freedesktop.portal.Request"
_SESSION_INTERFACE = "org.freedesktop.portal.Session"
_PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"
_TOKEN = re.compile(r"[^A-Za-z0-9_]")


def _unwrap(value: Any) -> Any:
    if isinstance(value, Variant):
        return _unwrap(value.value)
    if isinstance(value, Mapping):
        return {str(key): _unwrap(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_unwrap(item) for item in value]
    return value


def _create_session_options(session_token: str) -> dict[str, Variant]:
    return {"session_handle_token": Variant("s", session_token)}


def _select_sources_options(
    *,
    source_type: CaptureSourceType,
    restore_token: str,
    persistence_supported: bool,
    cursor_visible: bool,
) -> dict[str, Variant]:
    options = {
        "types": Variant("u", 2 if source_type is CaptureSourceType.WINDOW else 1),
        "multiple": Variant("b", False),
        "cursor_mode": Variant("u", 2 if cursor_visible else 1),
    }
    if persistence_supported:
        options["persist_mode"] = Variant("u", 2)
        if restore_token:
            options["restore_token"] = Variant("s", restore_token)
    return options


@dataclass(frozen=True, slots=True)
class PortalStream:
    node_id: int
    target_object: str
    target_is_serial: bool = False
    width: int = 0
    height: int = 0


@dataclass(frozen=True, slots=True)
class _PortalConnectionSnapshot:
    available: bool
    version: int = 0
    source_mask: int = 0
    message: str = ""


class _PortalOperationError(RuntimeError):
    def __init__(
        self,
        stage: str,
        message: str,
        *,
        response_code: int | None = None,
        error_name: str = "",
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.response_code = response_code
        self.error_name = error_name


@dataclass(slots=True)
class _PendingRequest:
    stage: str
    future: asyncio.Future[tuple[int, dict[str, Any]]]


class _PortalDbusClient:
    """ScreenCast protocol on one connected dbus-next session bus."""

    def __init__(self, bus: Any) -> None:
        self._bus = bus
        self._pending: dict[str, _PendingRequest] = {}
        self._closed_callbacks: dict[str, Callable[[], None]] = {}
        self._snapshot = _PortalConnectionSnapshot(False, message="Portal not initialized")
        self._bus.add_message_handler(self._message_received)

    @property
    def snapshot(self) -> _PortalConnectionSnapshot:
        return self._snapshot

    async def initialize(self) -> _PortalConnectionSnapshot:
        try:
            await self._add_match(
                f"type='signal',sender='{_PORTAL_SERVICE}',"
                f"interface='{_REQUEST_INTERFACE}',member='Response'"
            )
            await self._add_match(
                f"type='signal',sender='{_PORTAL_SERVICE}',"
                f"interface='{_SESSION_INTERFACE}',member='Closed'"
            )
            version = int(await self._property("version"))
            source_mask = int(await self._property("AvailableSourceTypes"))
        except Exception as error:
            self._snapshot = _PortalConnectionSnapshot(
                False, message=f"The ScreenCast portal is unavailable: {error}"
            )
        else:
            self._snapshot = _PortalConnectionSnapshot(True, version, source_mask)
        return self._snapshot

    async def create_session(self) -> str:
        session_token = self._token("narrator_session")
        code, values = await self._request(
            "CreateSession",
            "a{sv}",
            [_create_session_options(session_token)],
            stage="CreateSession",
        )
        self._ensure_success("CreateSession", code, values)
        handle = str(values.get("session_handle", ""))
        if not handle:
            raise _PortalOperationError(
                "CreateSession", "The portal returned an empty session handle"
            )
        return handle

    async def select_sources(
        self,
        session_handle: str,
        *,
        source_type: CaptureSourceType,
        restore_token: str,
        persistence_supported: bool,
        cursor_visible: bool,
    ) -> None:
        options = _select_sources_options(
            source_type=source_type,
            restore_token=restore_token,
            persistence_supported=persistence_supported,
            cursor_visible=cursor_visible,
        )
        code, values = await self._request(
            "SelectSources",
            "oa{sv}",
            [session_handle, options],
            stage="SelectSources",
        )
        self._ensure_success("SelectSources", code, values)

    async def start_session(self, session_handle: str) -> tuple[PortalStream, str]:
        code, values = await self._request(
            "Start",
            "osa{sv}",
            [session_handle, "", {}],
            stage="Start",
        )
        self._ensure_success("Start", code, values)
        streams = values.get("streams")
        if not isinstance(streams, (list, tuple)) or not streams:
            raise _PortalOperationError("Start", "The portal returned no PipeWire stream")
        try:
            stream = self.parse_stream(streams[0])
        except (TypeError, ValueError) as error:
            raise _PortalOperationError(
                "Start", f"The portal returned an invalid PipeWire stream: {error}"
            ) from error
        return stream, str(values.get("restore_token", ""))

    async def open_remote(self, session_handle: str) -> int:
        reply = await self._call(
            Message(
                destination=_PORTAL_SERVICE,
                path=_PORTAL_PATH,
                interface=_SCREENCAST_INTERFACE,
                member="OpenPipeWireRemote",
                signature="oa{sv}",
                body=[session_handle, {}],
            ),
            "OpenPipeWireRemote",
        )
        received = list(reply.unix_fds or [])
        try:
            if reply.signature != "h" or not reply.body:
                raise _PortalOperationError(
                    "OpenPipeWireRemote", "The portal returned no PipeWire descriptor"
                )
            index = int(reply.body[0])
            if index < 0 or index >= len(received):
                raise _PortalOperationError(
                    "OpenPipeWireRemote", "The portal returned an invalid PipeWire descriptor"
                )
            descriptor = os.dup(received[index])
            os.set_inheritable(descriptor, False)
            return descriptor
        finally:
            for descriptor in received:
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    def watch_session_closed(self, session_handle: str, callback: Callable[[], None]) -> None:
        self._closed_callbacks[session_handle] = callback

    async def close_session(self, session_handle: str) -> None:
        self._closed_callbacks.pop(session_handle, None)
        await self._call(
            Message(
                destination=_PORTAL_SERVICE,
                path=session_handle,
                interface=_SESSION_INTERFACE,
                member="Close",
            ),
            "Session.Close",
        )

    def cancel_pending(self, reason: str = "ScreenCast request cancelled") -> None:
        pending = list(self._pending.values())
        self._pending.clear()
        for request in pending:
            if not request.future.done():
                request.future.set_exception(_PortalOperationError(request.stage, reason))

    def close(self) -> None:
        self.cancel_pending("ScreenCast connection closed")
        self._closed_callbacks.clear()
        self._bus.remove_message_handler(self._message_received)

    async def _request(
        self,
        method: str,
        signature: str,
        body: list[Any],
        *,
        stage: str,
    ) -> tuple[int, dict[str, Any]]:
        handle_token = self._token("narrator_request")
        options = dict(body[-1])
        options["handle_token"] = Variant("s", handle_token)
        body[-1] = options
        predicted_path = self._request_path(handle_token)
        future = asyncio.get_running_loop().create_future()
        self._pending[predicted_path] = _PendingRequest(stage, future)
        try:
            reply = await self._call(
                Message(
                    destination=_PORTAL_SERVICE,
                    path=_PORTAL_PATH,
                    interface=_SCREENCAST_INTERFACE,
                    member=method,
                    signature=signature,
                    body=body,
                ),
                stage,
            )
            if reply.signature != "o" or not reply.body:
                raise _PortalOperationError(stage, "The portal returned no request handle")
            actual_path = str(reply.body[0])
            if actual_path != predicted_path:
                pending = self._pending.pop(predicted_path, None)
                if pending is not None:
                    self._pending[actual_path] = pending
            return await future
        finally:
            for path, pending in list(self._pending.items()):
                if pending.future is future:
                    self._pending.pop(path, None)

    async def _property(self, name: str) -> Any:
        reply = await self._call(
            Message(
                destination=_PORTAL_SERVICE,
                path=_PORTAL_PATH,
                interface=_PROPERTIES_INTERFACE,
                member="Get",
                signature="ss",
                body=[_SCREENCAST_INTERFACE, name],
            ),
            f"Properties.Get({name})",
        )
        if reply.signature != "v" or not reply.body:
            raise _PortalOperationError(
                f"Properties.Get({name})", "The portal returned an invalid property value"
            )
        return _unwrap(reply.body[0])

    async def _add_match(self, rule: str) -> None:
        await self._call(
            Message(
                destination="org.freedesktop.DBus",
                path="/org/freedesktop/DBus",
                interface="org.freedesktop.DBus",
                member="AddMatch",
                signature="s",
                body=[rule],
            ),
            "AddMatch",
        )

    async def _call(self, message: Message, stage: str) -> Message:
        try:
            reply = await self._bus.call(message)
        except Exception as error:
            raise _PortalOperationError(stage, str(error)) from error
        if reply is None:
            raise _PortalOperationError(stage, "The D-Bus call returned no reply")
        if reply.message_type is MessageType.ERROR:
            detail = str(reply.body[0]) if reply.body else "D-Bus method call failed"
            raise _PortalOperationError(
                stage, detail, error_name=str(reply.error_name or "")
            )
        if reply.message_type is not MessageType.METHOD_RETURN:
            raise _PortalOperationError(stage, "The D-Bus call returned an unexpected message")
        return reply

    def _message_received(self, message: Message) -> bool:
        if message.message_type is not MessageType.SIGNAL:
            return False
        if message.interface == _REQUEST_INTERFACE and message.member == "Response":
            pending = self._pending.pop(str(message.path or ""), None)
            if pending is None or pending.future.done():
                return False
            try:
                code = int(message.body[0])
                values = dict(_unwrap(message.body[1]))
            except (IndexError, TypeError, ValueError) as error:
                pending.future.set_exception(
                    _PortalOperationError(
                        pending.stage, f"The portal returned an invalid response: {error}"
                    )
                )
            else:
                pending.future.set_result((code, values))
            return True
        if message.interface == _SESSION_INTERFACE and message.member == "Closed":
            callback = self._closed_callbacks.pop(str(message.path or ""), None)
            if callback is not None:
                callback()
                return True
        return False

    @staticmethod
    def _ensure_success(stage: str, code: int, values: Mapping[str, Any]) -> None:
        if code == 0:
            return
        detail = str(values.get("error") or values.get("message") or "Portal request failed")
        raise _PortalOperationError(stage, detail, response_code=code)

    @staticmethod
    def parse_stream(value: Any) -> PortalStream:
        stream = _unwrap(value)
        if not isinstance(stream, (list, tuple)) or len(stream) != 2:
            raise ValueError("stream entry has an unexpected shape")
        node_id = int(stream[0])
        properties = stream[1] if isinstance(stream[1], Mapping) else {}
        serial = properties.get("pipewire-serial")
        target = str(int(serial)) if serial is not None else str(node_id)
        size = properties.get("size")
        width = height = 0
        if isinstance(size, (list, tuple)) and len(size) == 2:
            width, height = int(size[0]), int(size[1])
        return PortalStream(
            node_id=node_id,
            target_object=target,
            target_is_serial=serial is not None,
            width=width,
            height=height,
        )

    def _request_path(self, handle_token: str) -> str:
        sender = str(self._bus.unique_name or "").lstrip(":").replace(".", "_")
        return f"/org/freedesktop/portal/desktop/request/{sender}/{handle_token}"

    @staticmethod
    def _token(prefix: str) -> str:
        return _TOKEN.sub("_", f"{prefix}_{uuid4().hex}")


class _DbusNextPortalConnection:
    """Own one D-Bus connection and its asyncio loop for a portal lifetime."""

    def __init__(self, bus_factory: Callable[[], Any] | None = None) -> None:
        self._bus_factory = bus_factory or (
            lambda: MessageBus(bus_type=BusType.SESSION, negotiate_unix_fd=True)
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: _PortalDbusClient | None = None
        self._ready = threading.Event()
        self._shutdown_requested = threading.Event()
        self._snapshot = _PortalConnectionSnapshot(False, message="Connecting to portal")
        self._thread = threading.Thread(
            target=self._run, name="narrator-portal-dbus", daemon=True
        )
        self._thread.start()

    def snapshot(self, timeout: float = 3.0) -> _PortalConnectionSnapshot:
        self._ready.wait(timeout)
        return self._snapshot

    def create_session(self, callback: Callable[[str], None], error: Callable[[Exception], None]) -> None:
        self._submit(self._require_client().create_session(), callback, error)

    def select_sources(
        self,
        session_handle: str,
        *,
        source_type: CaptureSourceType,
        restore_token: str,
        persistence_supported: bool,
        cursor_visible: bool,
        callback: Callable[[], None],
        error: Callable[[Exception], None],
    ) -> None:
        self._submit(
            self._require_client().select_sources(
                session_handle,
                source_type=source_type,
                restore_token=restore_token,
                persistence_supported=persistence_supported,
                cursor_visible=cursor_visible,
            ),
            lambda _: callback(),
            error,
        )

    def start_session(
        self,
        session_handle: str,
        callback: Callable[[tuple[PortalStream, str]], None],
        error: Callable[[Exception], None],
    ) -> None:
        self._submit(self._require_client().start_session(session_handle), callback, error)

    def open_remote(
        self,
        session_handle: str,
        callback: Callable[[int], None],
        error: Callable[[Exception], None],
    ) -> None:
        self._submit(self._require_client().open_remote(session_handle), callback, error)

    def watch_session_closed(self, session_handle: str, callback: Callable[[], None]) -> None:
        loop = self._loop
        client = self._client
        if loop is not None and client is not None:
            loop.call_soon_threadsafe(client.watch_session_closed, session_handle, callback)

    def close_session(self, session_handle: str) -> None:
        client = self._client
        if client is not None:
            self._submit(client.close_session(session_handle), lambda _: None, self._log_error)

    def cancel_pending(self) -> None:
        loop = self._loop
        client = self._client
        if loop is not None and client is not None:
            loop.call_soon_threadsafe(client.cancel_pending)

    def shutdown(self) -> None:
        self._shutdown_requested.set()
        loop = self._loop
        if loop is None:
            return
        client = self._client
        if client is not None:
            loop.call_soon_threadsafe(client.cancel_pending, "ScreenCast transport stopped")
        loop.call_soon_threadsafe(lambda: None)
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        bus: Any = None
        try:
            bus = self._bus_factory()
            loop.run_until_complete(bus.connect())
            self._client = _PortalDbusClient(bus)
            self._snapshot = loop.run_until_complete(self._client.initialize())
        except Exception as error:
            self._snapshot = _PortalConnectionSnapshot(
                False, message=f"The desktop portal session bus is unavailable: {error}"
            )
        try:
            loop.run_until_complete(self._wait_for_shutdown())
        finally:
            if self._client is not None:
                self._client.close()
            tasks = asyncio.all_tasks(loop)
            for task in tasks:
                task.cancel()
            if tasks:
                loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
            if bus is not None:
                try:
                    bus.disconnect()
                except Exception:
                    logger.debug("Could not disconnect narrator portal bus", exc_info=True)
            loop.close()

    async def _wait_for_shutdown(self) -> None:
        self._ready.set()
        while not self._shutdown_requested.is_set():
            await asyncio.sleep(0.05)

    def _require_client(self) -> _PortalDbusClient:
        self._ready.wait(3.0)
        if self._client is None:
            raise _PortalOperationError("Connect", self._snapshot.message)
        return self._client

    def _submit(
        self,
        coroutine: Coroutine[Any, Any, Any],
        callback: Callable[[Any], None],
        error: Callable[[Exception], None],
    ) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            coroutine.close()
            error(_PortalOperationError("Connect", self._snapshot.message))
            return
        future: ConcurrentFuture[Any] = asyncio.run_coroutine_threadsafe(coroutine, loop)

        def completed(result: ConcurrentFuture[Any]) -> None:
            try:
                value = result.result()
            except Exception as exception:
                error(exception)
            else:
                callback(value)

        future.add_done_callback(completed)

    @staticmethod
    def _log_error(error: Exception) -> None:
        logger.debug("Portal cleanup failed: %s", error)


class QtScreenCastPortal(QObject):
    """Qt-thread callback facade over the typed dbus-next transport."""

    _queued = Signal(object, object)

    def __init__(
        self,
        parent: QObject | None = None,
        connection: _DbusNextPortalConnection | None = None,
        connection_factory: Callable[[], _DbusNextPortalConnection] | None = None,
    ) -> None:
        super().__init__(parent)
        self._connection_factory = connection_factory or _DbusNextPortalConnection
        self._connection = connection
        self._queued.connect(self._run_queued, Qt.ConnectionType.QueuedConnection)

    def capabilities(self, transport_available: bool, transport_message: str) -> CaptureCapabilities:
        snapshot = self._ensure_connection().snapshot()
        sources: set[CaptureSourceType] = set()
        if snapshot.source_mask & 1:
            sources.add(CaptureSourceType.MONITOR)
        if snapshot.source_mask & 2:
            sources.add(CaptureSourceType.WINDOW)
        if not snapshot.available:
            return CaptureCapabilities(False, message=snapshot.message)
        if not transport_available:
            return CaptureCapabilities(
                False,
                portal_version=snapshot.version,
                source_types=frozenset(sources),
                persistence_supported=snapshot.version >= 4,
                message=transport_message,
            )
        if not sources:
            return CaptureCapabilities(
                False,
                portal_version=snapshot.version,
                message="The ScreenCast portal exposes no monitor or window sources",
            )
        return CaptureCapabilities(
            True,
            portal_version=snapshot.version,
            source_types=frozenset(sources),
            persistence_supported=snapshot.version >= 4,
            message="Wayland portal capture is available",
        )

    def create_session(
        self,
        callback: Callable[[str], None],
        error_callback: Callable[[int, str], None],
    ) -> None:
        try:
            self._ensure_connection().create_session(
                lambda handle: self._dispatch(callback, handle),
                lambda error: self._dispatch_portal_error(error_callback, error),
            )
        except Exception as error:
            self._dispatch_portal_error(error_callback, error)

    def select_sources(
        self,
        session_handle: str,
        *,
        source_type: CaptureSourceType,
        restore_token: str,
        persistence_supported: bool,
        cursor_visible: bool,
        callback: Callable[[], None],
        error_callback: Callable[[int, str], None],
    ) -> None:
        try:
            self._ensure_connection().select_sources(
                session_handle,
                source_type=source_type,
                restore_token=restore_token,
                persistence_supported=persistence_supported,
                cursor_visible=cursor_visible,
                callback=lambda: self._dispatch(callback),
                error=lambda error: self._dispatch_portal_error(error_callback, error),
            )
        except Exception as error:
            self._dispatch_portal_error(error_callback, error)

    def start_session(
        self,
        session_handle: str,
        *,
        callback: Callable[[PortalStream, str], None],
        error_callback: Callable[[int, str], None],
    ) -> None:
        try:
            self._ensure_connection().start_session(
                session_handle,
                lambda result: self._dispatch(callback, result[0], result[1]),
                lambda error: self._dispatch_portal_error(error_callback, error),
            )
        except Exception as error:
            self._dispatch_portal_error(error_callback, error)

    def open_remote(
        self,
        session_handle: str,
        callback: Callable[[int], None],
        error_callback: Callable[[str], None],
    ) -> None:
        try:
            self._ensure_connection().open_remote(
                session_handle,
                lambda descriptor: self._dispatch(callback, descriptor),
                lambda error: self._dispatch_open_error(error_callback, error),
            )
        except Exception as error:
            self._dispatch_open_error(error_callback, error)

    def watch_session_closed(self, session_handle: str, callback: Callable[[], None]) -> None:
        self._ensure_connection().watch_session_closed(
            session_handle, lambda: self._dispatch(callback)
        )

    def close_session(self, session_handle: str) -> None:
        if session_handle:
            connection = self._connection
            if connection is not None:
                connection.close_session(session_handle)

    def cancel_pending(self) -> None:
        connection = self._connection
        if connection is not None:
            connection.cancel_pending()

    def shutdown(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is not None:
            connection.shutdown()

    def _ensure_connection(self) -> _DbusNextPortalConnection:
        connection = self._connection
        if connection is None:
            connection = self._connection_factory()
            self._connection = connection
        return connection

    def _dispatch_portal_error(
        self, callback: Callable[[int, str], None], error: Exception
    ) -> None:
        if isinstance(error, _PortalOperationError):
            code = error.response_code if error.response_code is not None else 2
            logger.warning(
                "Narrator portal D-Bus failure stage=%s responseCode=%s dbusError=%s detail=%s",
                error.stage,
                error.response_code,
                error.error_name or "none",
                error,
            )
        else:
            code = 2
            logger.warning("Narrator portal D-Bus failure: %s", error)
        self._dispatch(callback, code, str(error))

    def _dispatch_open_error(self, callback: Callable[[str], None], error: Exception) -> None:
        if isinstance(error, _PortalOperationError):
            logger.warning(
                "Narrator portal D-Bus failure stage=%s dbusError=%s detail=%s",
                error.stage,
                error.error_name or "none",
                error,
            )
        self._dispatch(callback, str(error))

    def _dispatch(self, callback: Callable[..., None], *arguments: Any) -> None:
        self._queued.emit(callback, arguments)

    @Slot(object, object)
    def _run_queued(self, callback: Callable[..., None], arguments: tuple[Any, ...]) -> None:
        callback(*arguments)


class QtPortalScreenCastBackend(QObject):
    """Compose the ScreenCast portal with GStreamer's PipeWire transport."""

    def __init__(
        self,
        portal: QtScreenCastPortal | None = None,
        transport: GStreamerPipeWireTransport | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._portal = portal or QtScreenCastPortal(self)
        self._transport = transport or GStreamerPipeWireTransport()
        self._attempt = 0
        self._session_handle = ""
        self._remote_fd = -1
        self._request: CaptureRequest | None = None
        self._frame_callback: FrameCallback | None = None
        self._started_callback: Callable[[CaptureSessionInfo], None] | None = None
        self._state_callback: StateCallback | None = None
        self._stream: PortalStream | None = None
        self._restore_token = ""
        self._stopping = False

    def capabilities(self) -> CaptureCapabilities:
        return self._portal.capabilities(self._transport.available, self._transport.message)

    def start(
        self,
        request: CaptureRequest,
        *,
        frame_callback: FrameCallback,
        started_callback: Callable[[CaptureSessionInfo], None],
        state_callback: StateCallback,
    ) -> None:
        self._stop_capture(shutdown_portal=False)
        self._attempt += 1
        attempt = self._attempt
        self._stopping = False
        self._request = request
        self._frame_callback = frame_callback
        self._started_callback = started_callback
        self._state_callback = state_callback
        state_callback(CaptureState.STARTING, "")
        self._portal.create_session(
            lambda handle: self._session_created(handle, attempt),
            lambda code, message: self._portal_error(code, message, attempt),
        )

    def stop(self) -> None:
        self._stop_capture(shutdown_portal=True)

    def _stop_capture(self, *, shutdown_portal: bool) -> None:
        self._attempt += 1
        self._stopping = True
        self._transport.stop()
        if self._remote_fd >= 0:
            os.close(self._remote_fd)
            self._remote_fd = -1
        if self._session_handle:
            self._portal.close_session(self._session_handle)
        cancel = getattr(self._portal, "cancel_pending", None)
        if callable(cancel):
            cancel()
        self._session_handle = ""
        self._request = None
        self._frame_callback = None
        self._started_callback = None
        self._state_callback = None
        self._stream = None
        self._restore_token = ""
        if shutdown_portal:
            shutdown = getattr(self._portal, "shutdown", None)
            if callable(shutdown):
                shutdown()

    def close(self) -> None:
        self.stop()

    def _session_created(self, handle: str, attempt: int) -> None:
        request = self._current(attempt)
        if request is None:
            if handle:
                self._portal.close_session(handle)
            return
        if not handle:
            self._portal_error(2, "The portal returned an empty session handle", attempt)
            return
        self._session_handle = handle
        self._portal.watch_session_closed(handle, lambda: self._session_closed(attempt))
        callback = self._state_callback
        if callback is not None:
            callback(CaptureState.SELECTING_SOURCE, "")
        capabilities = self.capabilities()
        self._portal.select_sources(
            handle,
            source_type=request.source_type,
            restore_token=request.restore_token,
            persistence_supported=capabilities.persistence_supported,
            cursor_visible=request.cursor_visible,
            callback=lambda: self._sources_selected(attempt),
            error_callback=lambda code, message: self._portal_error(
                code, message, attempt, permission_request=True
            ),
        )

    def _sources_selected(self, attempt: int) -> None:
        if self._current(attempt) is None:
            return
        self._portal.start_session(
            self._session_handle,
            callback=lambda stream, token: self._portal_started(stream, token, attempt),
            error_callback=lambda code, message: self._portal_error(code, message, attempt),
        )

    def _portal_started(self, stream: PortalStream, restore_token: str, attempt: int) -> None:
        if self._current(attempt) is None:
            return
        self._stream = stream
        self._restore_token = restore_token
        self._portal.open_remote(
            self._session_handle,
            callback=lambda descriptor: self._remote_opened(descriptor, attempt),
            error_callback=lambda message: self._capture_error(message, attempt),
        )

    def _remote_opened(self, descriptor: int, attempt: int) -> None:
        request = self._current(attempt)
        if request is None:
            os.close(descriptor)
            return
        stream = self._stream
        callback = self._frame_callback
        if stream is None or callback is None:
            os.close(descriptor)
            return
        self._remote_fd = descriptor
        try:
            self._transport.start(
                remote_fd=descriptor,
                target_object=stream.target_object,
                target_is_serial=stream.target_is_serial,
                session_id=request.session_id,
                generation=request.generation,
                sampling_hz=request.sampling_hz,
                frame_callback=lambda frame: self._frame(frame, attempt),
                ready_callback=lambda: self._transport_ready(attempt),
                state_callback=lambda state, message: self._transport_state(
                    state, message, attempt
                ),
            )
        except Exception as error:
            logger.warning("Could not start GStreamer PipeWire capture: %s", error)
            self._capture_error(f"PipeWire connection failed: {error}", attempt)

    def _transport_ready(self, attempt: int) -> None:
        request = self._current(attempt)
        callback = self._started_callback
        stream = self._stream
        if request is None or callback is None or stream is None:
            return
        callback(
            CaptureSessionInfo(
                session_id=request.session_id,
                source_type=request.source_type,
                stream_id=stream.target_object,
                restore_token=self._restore_token,
            )
        )

    def _frame(self, frame: Any, attempt: int) -> None:
        if self._current(attempt) is None or self._frame_callback is None:
            return
        self._frame_callback(frame)

    def _transport_state(self, state: CaptureState, message: str, attempt: int) -> None:
        if self._current(attempt) is None or self._state_callback is None:
            return
        logger.warning("Narrator capture stream ended: %s", message)
        self._state_callback(state, message)

    def _session_closed(self, attempt: int) -> None:
        if self._current(attempt) is None or self._stopping:
            return
        self._session_handle = ""
        if self._state_callback is not None:
            self._state_callback(CaptureState.SOURCE_LOST, "The portal capture session closed")
        if self._current(attempt) is not None:
            self.stop()

    def _portal_error(
        self,
        code: int,
        message: str,
        attempt: int,
        *,
        permission_request: bool = False,
    ) -> None:
        request = self._current(attempt)
        callback = self._state_callback
        if request is None or callback is None:
            return
        detail = message.strip() or "The ScreenCast portal request failed"
        if code == 1:
            state = CaptureState.CANCELLED
            detail = "Screen capture selection was cancelled"
        elif request.restore_token:
            state = CaptureState.RESTORE_FAILED
        elif permission_request:
            state = CaptureState.PERMISSION_DENIED
        else:
            state = CaptureState.ERROR
        logger.warning(
            "Narrator portal request failed state=%s responseCode=%s detail=%s",
            state.value,
            code,
            detail,
        )
        callback(state, detail)
        if self._current(attempt) is not None:
            self.stop()

    def _capture_error(self, message: str, attempt: int) -> None:
        if self._current(attempt) is None or self._state_callback is None:
            return
        detail = message.strip() or "The PipeWire capture transport failed"
        logger.warning("Narrator capture transport failed: %s", detail)
        self._state_callback(CaptureState.ERROR, detail)
        if self._current(attempt) is not None:
            self.stop()

    def _current(self, attempt: int) -> CaptureRequest | None:
        if attempt != self._attempt or self._stopping:
            return None
        return self._request


__all__ = ["PortalStream", "QtPortalScreenCastBackend", "QtScreenCastPortal"]
