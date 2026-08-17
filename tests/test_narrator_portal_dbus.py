from __future__ import annotations

import asyncio
from io import BytesIO
import os
import threading

import pytest
from dbus_next import Message, MessageType, Variant
from dbus_next._private.unmarshaller import Unmarshaller

from game_optimization_linux.models.narrator import CaptureSourceType
from game_optimization_linux.services.narrator_portal import (
    _DbusNextPortalConnection,
    _PortalDbusClient,
    _PortalConnectionSnapshot,
    _PortalOperationError,
    QtScreenCastPortal,
    _select_sources_options,
)


class _FakeBus:
    unique_name = ":1.77"

    def __init__(self) -> None:
        self.handlers = []
        self.calls: list[Message] = []
        self.responses: dict[str, tuple[int, dict[str, Variant]] | None] = {}
        self.remote_fd = -1

    def add_message_handler(self, callback) -> None:
        self.handlers.append(callback)

    async def connect(self):
        return self

    def disconnect(self) -> None:
        return

    def remove_message_handler(self, callback) -> None:
        self.handlers.remove(callback)

    async def call(self, message: Message) -> Message:
        self.calls.append(message)
        if message.member == "Get":
            value = 5 if message.body[1] == "version" else 3
            return Message(
                message_type=MessageType.METHOD_RETURN,
                reply_serial=1,
                signature="v",
                body=[Variant("u", value)],
            )
        if message.member == "OpenPipeWireRemote":
            return Message(
                message_type=MessageType.METHOD_RETURN,
                reply_serial=1,
                signature="h",
                body=[0],
                unix_fds=[self.remote_fd],
            )
        if message.member in {"CreateSession", "SelectSources", "Start"}:
            token = message.body[-1]["handle_token"].value
            path = f"/org/freedesktop/portal/desktop/request/1_77/{token}"
            response = self.responses.get(message.member, (0, {}))
            if response is not None:
                code, values = response
                signal = Message(
                    message_type=MessageType.SIGNAL,
                    path=path,
                    interface="org.freedesktop.portal.Request",
                    member="Response",
                    signature="ua{sv}",
                    body=[code, values],
                )
                asyncio.get_running_loop().call_soon(self.emit, signal)
            return Message(
                message_type=MessageType.METHOD_RETURN,
                reply_serial=1,
                signature="o",
                body=[path],
            )
        return Message(message_type=MessageType.METHOD_RETURN, reply_serial=1)

    def emit(self, message: Message) -> None:
        for handler in list(self.handlers):
            handler(message)


def _run(coroutine):
    return asyncio.run(coroutine)


def test_select_sources_options_have_exact_portal_signatures() -> None:
    options = _select_sources_options(
        source_type=CaptureSourceType.WINDOW,
        restore_token="saved-token",
        persistence_supported=True,
        cursor_visible=True,
    )
    message = Message(
        destination="org.freedesktop.portal.Desktop",
        path="/org/freedesktop/portal/desktop",
        interface="org.freedesktop.portal.ScreenCast",
        member="SelectSources",
        signature="oa{sv}",
        body=["/org/freedesktop/portal/desktop/session/test", options],
        serial=1,
    )
    decoded = Unmarshaller(BytesIO(message._marshall())).unmarshall()
    assert {name: value.signature for name, value in decoded.body[1].items()} == {
        "types": "u",
        "multiple": "b",
        "cursor_mode": "u",
        "persist_mode": "u",
        "restore_token": "s",
    }


def test_create_session_waits_for_request_response() -> None:
    async def scenario() -> None:
        bus = _FakeBus()
        bus.responses["CreateSession"] = (
            0,
            {
                "session_handle": Variant(
                    "o", "/org/freedesktop/portal/desktop/session/1_77/narrator"
                )
            },
        )
        client = _PortalDbusClient(bus)
        assert await client.create_session() == (
            "/org/freedesktop/portal/desktop/session/1_77/narrator"
        )
        assert bus.calls[-1].signature == "a{sv}"
        assert bus.calls[-1].body[0]["session_handle_token"].signature == "s"
        assert bus.calls[-1].body[0]["handle_token"].signature == "s"

    _run(scenario())


def test_select_sources_uses_uint32_and_reports_portal_failure() -> None:
    async def scenario() -> None:
        bus = _FakeBus()
        client = _PortalDbusClient(bus)
        await client.select_sources(
            "/org/freedesktop/portal/desktop/session/test",
            source_type=CaptureSourceType.MONITOR,
            restore_token="restore",
            persistence_supported=True,
            cursor_visible=False,
        )
        sent = bus.calls[-1]
        assert sent.signature == "oa{sv}"
        assert sent.body[1]["types"].signature == "u"
        assert sent.body[1]["cursor_mode"].signature == "u"
        assert sent.body[1]["persist_mode"].signature == "u"
        assert sent.body[1]["multiple"].signature == "b"

        bus.responses["SelectSources"] = (
            2,
            {"error": Variant("s", "Invalid option type")},
        )
        with pytest.raises(_PortalOperationError) as caught:
            await client.select_sources(
                "/org/freedesktop/portal/desktop/session/test",
                source_type=CaptureSourceType.WINDOW,
                restore_token="",
                persistence_supported=False,
                cursor_visible=True,
            )
        assert caught.value.stage == "SelectSources"
        assert caught.value.response_code == 2
        assert "Invalid option type" in str(caught.value)

    _run(scenario())


def test_start_parses_pipewire_serial_dimensions_and_restore_token() -> None:
    async def scenario() -> None:
        bus = _FakeBus()
        bus.responses["Start"] = (
            0,
            {
                "streams": Variant(
                    "a(ua{sv})",
                    [
                        [
                            44,
                            {
                                "pipewire-serial": Variant("u", 9123),
                                "size": Variant("(ii)", [1920, 1080]),
                            },
                        ]
                    ],
                ),
                "restore_token": Variant("s", "new-token"),
            },
        )
        client = _PortalDbusClient(bus)
        stream, token = await client.start_session(
            "/org/freedesktop/portal/desktop/session/test"
        )
        assert (stream.node_id, stream.target_object, stream.target_is_serial) == (
            44,
            "9123",
            True,
        )
        assert (stream.width, stream.height, token) == (1920, 1080, "new-token")

    _run(scenario())


def test_open_remote_duplicates_and_owns_unix_descriptor() -> None:
    async def scenario() -> None:
        bus = _FakeBus()
        read_fd, write_fd = os.pipe()
        os.close(write_fd)
        bus.remote_fd = read_fd
        client = _PortalDbusClient(bus)
        owned = await client.open_remote(
            "/org/freedesktop/portal/desktop/session/test"
        )
        try:
            with pytest.raises(OSError):
                os.fstat(read_fd)
            os.fstat(owned)
            assert not os.get_inheritable(owned)
        finally:
            os.close(owned)

    _run(scenario())


def test_cancel_pending_rejects_late_response() -> None:
    async def scenario() -> None:
        bus = _FakeBus()
        bus.responses["SelectSources"] = None
        client = _PortalDbusClient(bus)
        task = asyncio.create_task(
            client.select_sources(
                "/org/freedesktop/portal/desktop/session/test",
                source_type=CaptureSourceType.WINDOW,
                restore_token="",
                persistence_supported=False,
                cursor_visible=False,
            )
        )
        await asyncio.sleep(0)
        request_path = next(iter(client._pending))
        client.cancel_pending("new capture attempt started")
        with pytest.raises(_PortalOperationError, match="new capture attempt"):
            await task
        bus.emit(
            Message(
                message_type=MessageType.SIGNAL,
                path=request_path,
                interface="org.freedesktop.portal.Request",
                member="Response",
                signature="ua{sv}",
                body=[0, {}],
            )
        )
        assert not client._pending

    _run(scenario())


def test_session_closed_is_delivered_once() -> None:
    bus = _FakeBus()
    client = _PortalDbusClient(bus)
    path = "/org/freedesktop/portal/desktop/session/1_77/narrator"
    closed = []
    client.watch_session_closed(path, lambda: closed.append(path))
    signal = Message(
        message_type=MessageType.SIGNAL,
        path=path,
        interface="org.freedesktop.portal.Session",
        member="Closed",
    )
    bus.emit(signal)
    bus.emit(signal)
    assert closed == [path]


def test_qt_portal_replaces_connection_after_shutdown() -> None:
    class Connection:
        def __init__(self) -> None:
            self.shutdown_count = 0

        def snapshot(self):
            return _PortalConnectionSnapshot(True, version=5, source_mask=3)

        def shutdown(self) -> None:
            self.shutdown_count += 1

    first = Connection()
    second = Connection()
    created = []

    def factory():
        created.append(second)
        return second

    portal = QtScreenCastPortal(connection=first, connection_factory=factory)
    assert portal.capabilities(True, "").available
    portal.shutdown()
    assert first.shutdown_count == 1
    assert portal.capabilities(True, "").available
    assert created == [second]


def test_connection_thread_stops_when_initial_connect_fails() -> None:
    class BrokenBus:
        async def connect(self):
            raise OSError("session bus unavailable")

    connection = _DbusNextPortalConnection(bus_factory=BrokenBus)
    assert not connection.snapshot().available
    connection.shutdown()
    assert not connection._thread.is_alive()
    assert not any(
        thread.name == "narrator-portal-dbus" and thread.is_alive()
        for thread in threading.enumerate()
        if thread is connection._thread
    )


def test_connection_is_ready_for_requests_when_snapshot_returns() -> None:
    bus = _FakeBus()
    bus.responses["CreateSession"] = (
        0,
        {"session_handle": Variant("o", "/portal/session/ready")},
    )
    connection = _DbusNextPortalConnection(bus_factory=lambda: bus)
    completed = threading.Event()
    result = []
    errors = []
    assert connection.snapshot().available
    connection.create_session(
        lambda handle: (result.append(handle), completed.set()),
        lambda error: (errors.append(error), completed.set()),
    )
    assert completed.wait(1.0)
    connection.shutdown()
    assert result == ["/portal/session/ready"]
    assert not errors
    assert not connection._thread.is_alive()
