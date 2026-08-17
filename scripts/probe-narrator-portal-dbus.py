#!/usr/bin/env python3
"""Verify the wire signatures used by the ScreenCast SelectSources call."""

from __future__ import annotations

from io import BytesIO

from dbus_next import Message, Variant
from dbus_next._private.unmarshaller import Unmarshaller


def main() -> int:
    options = {
        "types": Variant("u", 3),
        "multiple": Variant("b", False),
        "cursor_mode": Variant("u", 2),
        "persist_mode": Variant("u", 2),
        "restore_token": Variant("s", "probe-token"),
    }
    message = Message(
        destination="org.freedesktop.portal.Desktop",
        path="/org/freedesktop/portal/desktop",
        interface="org.freedesktop.portal.ScreenCast",
        member="SelectSources",
        signature="oa{sv}",
        body=["/org/freedesktop/portal/desktop/session/probe/session", options],
        serial=1,
    )
    decoded = Unmarshaller(BytesIO(message._marshall())).unmarshall()
    signatures = {
        name: value.signature for name, value in decoded.body[1].items()
    }
    expected = {
        "types": "u",
        "multiple": "b",
        "cursor_mode": "u",
        "persist_mode": "u",
        "restore_token": "s",
    }
    print(signatures)
    if signatures != expected:
        raise SystemExit(f"unexpected SelectSources signatures: {signatures}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
