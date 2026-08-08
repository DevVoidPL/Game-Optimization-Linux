"""SDL3 gamepad provider with a deterministic no-SDL fallback.

Only this module knows the SDL ABI.  The application consumes neutral device
and event values and therefore remains fully usable when SDL3 is absent.
"""

from __future__ import annotations

from collections import deque
import ctypes
from ctypes.util import find_library
import logging
from pathlib import Path
from typing import Protocol, Sequence

from ..models import GamepadDevice, GamepadEvent, GamepadType


logger = logging.getLogger(__name__)

SDL_INIT_GAMEPAD = 0x00002000
SDL_EVENT_GAMEPAD_AXIS_MOTION = 0x650
SDL_EVENT_GAMEPAD_BUTTON_DOWN = 0x651
SDL_EVENT_GAMEPAD_BUTTON_UP = 0x652
SDL_EVENT_GAMEPAD_ADDED = 0x653
SDL_EVENT_GAMEPAD_REMOVED = 0x654
SDL_EVENT_GAMEPAD_REMAPPED = 0x655

_BUTTON_NAMES = {
    0: "south", 1: "east", 2: "west", 3: "north", 4: "back",
    5: "guide", 6: "start", 7: "left_stick", 8: "right_stick",
    9: "left_shoulder", 10: "right_shoulder", 11: "dpad_up",
    12: "dpad_down", 13: "dpad_left", 14: "dpad_right",
}
_AXIS_NAMES = {0: "left_x", 1: "left_y", 2: "right_x", 3: "right_y", 4: "left_trigger", 5: "right_trigger"}


class GamepadProvider(Protocol):
    @property
    def available(self) -> bool: ...
    @property
    def status(self) -> str: ...
    def start(self) -> Sequence[GamepadDevice]: ...
    def poll_events(self, limit: int = 256) -> Sequence[GamepadEvent]: ...
    def list_devices(self) -> Sequence[GamepadDevice]: ...
    def close(self) -> None: ...


class SDL3Unavailable(RuntimeError):
    """SDL3 could not be loaded or initialized."""


class _SDLGamepadDeviceEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("reserved", ctypes.c_uint32), ("timestamp", ctypes.c_uint64), ("which", ctypes.c_uint32)]


class _SDLGamepadButtonEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("reserved", ctypes.c_uint32), ("timestamp", ctypes.c_uint64), ("which", ctypes.c_uint32), ("button", ctypes.c_uint8), ("down", ctypes.c_bool), ("padding1", ctypes.c_uint8), ("padding2", ctypes.c_uint8)]


class _SDLGamepadAxisEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("reserved", ctypes.c_uint32), ("timestamp", ctypes.c_uint64), ("which", ctypes.c_uint32), ("axis", ctypes.c_uint8), ("padding1", ctypes.c_uint8), ("padding2", ctypes.c_uint8), ("padding3", ctypes.c_uint8), ("value", ctypes.c_int16), ("padding4", ctypes.c_uint16)]


class _SDLEvent(ctypes.Union):
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("gdevice", _SDLGamepadDeviceEvent),
        ("gbutton", _SDLGamepadButtonEvent),
        ("gaxis", _SDLGamepadAxisEvent),
        ("padding", ctypes.c_uint8 * 128),
    ]


def _decode(value: bytes | None, fallback: str = "Unknown controller") -> str:
    if not value:
        return fallback
    return value.decode("utf-8", errors="replace").strip() or fallback


def _classify_gamepad(sdl_type: int, name: str) -> GamepadType:
    normalized = name.casefold()
    if "steam deck" in normalized:
        return GamepadType.STEAM_DECK
    if sdl_type in {2, 3} or "xbox" in normalized or "xinput" in normalized:
        return GamepadType.XBOX
    if sdl_type in {4, 5, 6} or any(value in normalized for value in ("playstation", "dualshock", "dualsense")):
        return GamepadType.PLAYSTATION
    if sdl_type in {7, 8, 9, 10, 11} or "nintendo" in normalized or "joy-con" in normalized:
        return GamepadType.NINTENDO
    if sdl_type == 12 or "steam controller" in normalized:
        return GamepadType.STEAM
    if sdl_type == 1:
        return GamepadType.GENERIC
    return GamepadType.UNKNOWN


class SDL3GamepadProvider:
    """Bounded, non-blocking SDL event polling on the Qt main thread."""

    def __init__(self, library: object | None = None, *, mapping_file: Path | None = None) -> None:
        self._library = library or self._load_library()
        self._mapping_file = mapping_file
        self._handles: dict[int, ctypes.c_void_p] = {}
        self._devices: dict[int, GamepadDevice] = {}
        self._started = False
        self._closed = False
        self._configure_abi()

    @staticmethod
    def _load_library() -> object:
        candidates = [find_library("SDL3"), "libSDL3.so.0", "libSDL3.so"]
        errors: list[str] = []
        for candidate in candidates:
            if not candidate:
                continue
            try:
                return ctypes.CDLL(candidate)
            except OSError as error:
                errors.append(str(error))
        raise SDL3Unavailable("SDL3 library not found" + (f": {errors[-1]}" if errors else ""))

    def _configure_abi(self) -> None:
        required = (
            "SDL_InitSubSystem", "SDL_QuitSubSystem", "SDL_GetGamepads",
            "SDL_OpenGamepad", "SDL_CloseGamepad", "SDL_GetGamepadName",
            "SDL_GetGamepadType", "SDL_GetGamepadMapping", "SDL_PollEvent",
            "SDL_GetGamepadPowerInfo", "SDL_free", "SDL_GetError",
        )
        missing = [name for name in required if not hasattr(self._library, name)]
        if missing:
            raise SDL3Unavailable(f"SDL3 is missing required symbols: {', '.join(missing)}")
        lib = self._library
        lib.SDL_InitSubSystem.argtypes = [ctypes.c_uint32]
        lib.SDL_InitSubSystem.restype = ctypes.c_bool
        lib.SDL_QuitSubSystem.argtypes = [ctypes.c_uint32]
        lib.SDL_GetGamepads.argtypes = [ctypes.POINTER(ctypes.c_int)]
        lib.SDL_GetGamepads.restype = ctypes.POINTER(ctypes.c_uint32)
        lib.SDL_OpenGamepad.argtypes = [ctypes.c_uint32]
        lib.SDL_OpenGamepad.restype = ctypes.c_void_p
        lib.SDL_CloseGamepad.argtypes = [ctypes.c_void_p]
        lib.SDL_GetGamepadName.argtypes = [ctypes.c_void_p]
        lib.SDL_GetGamepadName.restype = ctypes.c_char_p
        lib.SDL_GetGamepadType.argtypes = [ctypes.c_void_p]
        lib.SDL_GetGamepadType.restype = ctypes.c_int
        lib.SDL_GetGamepadMapping.argtypes = [ctypes.c_void_p]
        lib.SDL_GetGamepadMapping.restype = ctypes.c_void_p
        lib.SDL_GetGamepadPowerInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
        lib.SDL_GetGamepadPowerInfo.restype = ctypes.c_int
        lib.SDL_PollEvent.argtypes = [ctypes.POINTER(_SDLEvent)]
        lib.SDL_PollEvent.restype = ctypes.c_bool
        lib.SDL_free.argtypes = [ctypes.c_void_p]
        lib.SDL_GetError.restype = ctypes.c_char_p
        if hasattr(lib, "SDL_AddGamepadMappingsFromFile"):
            lib.SDL_AddGamepadMappingsFromFile.argtypes = [ctypes.c_char_p]
            lib.SDL_AddGamepadMappingsFromFile.restype = ctypes.c_int

    @property
    def available(self) -> bool:
        return True

    @property
    def status(self) -> str:
        return "Available"

    def _error(self) -> str:
        return _decode(self._library.SDL_GetError(), "unknown SDL3 error")

    def start(self) -> Sequence[GamepadDevice]:
        if self._started:
            return self.list_devices()
        if not self._library.SDL_InitSubSystem(SDL_INIT_GAMEPAD):
            raise SDL3Unavailable(f"SDL3 gamepad initialization failed: {self._error()}")
        self._started = True
        self._closed = False
        if self._mapping_file and self._mapping_file.is_file() and hasattr(self._library, "SDL_AddGamepadMappingsFromFile"):
            count = self._library.SDL_AddGamepadMappingsFromFile(str(self._mapping_file).encode())
            if count < 0:
                logger.warning("Could not load SDL gamepad mappings: %s", self._error())
        count = ctypes.c_int(0)
        identifiers = self._library.SDL_GetGamepads(ctypes.byref(count))
        try:
            for index in range(max(0, count.value)):
                self._open(int(identifiers[index]))
        finally:
            if identifiers:
                self._library.SDL_free(ctypes.cast(identifiers, ctypes.c_void_p))
        return self.list_devices()

    def _open(self, instance_id: int) -> GamepadDevice | None:
        if instance_id in self._handles:
            return self._devices.get(instance_id)
        handle = self._library.SDL_OpenGamepad(instance_id)
        if not handle:
            logger.warning("SDL3 could not open gamepad %s: %s", instance_id, self._error())
            return None
        pointer = ctypes.c_void_p(handle)
        self._handles[instance_id] = pointer
        name = _decode(self._library.SDL_GetGamepadName(pointer))
        gamepad_type = _classify_gamepad(self._library.SDL_GetGamepadType(pointer), name)
        mapping_pointer = self._library.SDL_GetGamepadMapping(pointer)
        try:
            mapping_status = "Mapped" if mapping_pointer and ctypes.string_at(mapping_pointer) else "Fallback mapping"
        finally:
            if mapping_pointer:
                self._library.SDL_free(mapping_pointer)
        battery = ctypes.c_int(-1)
        power_state = self._library.SDL_GetGamepadPowerInfo(pointer, ctypes.byref(battery))
        battery_percent = battery.value if power_state > 0 and 0 <= battery.value <= 100 else None
        device = GamepadDevice(instance_id, name, gamepad_type, mapping_status, battery_percent)
        self._devices[instance_id] = device
        return device

    def _remove(self, instance_id: int) -> None:
        handle = self._handles.pop(instance_id, None)
        if handle:
            self._library.SDL_CloseGamepad(handle)
        self._devices.pop(instance_id, None)

    def poll_events(self, limit: int = 256) -> Sequence[GamepadEvent]:
        if not self._started or self._closed:
            return ()
        results: list[GamepadEvent] = []
        event = _SDLEvent()
        for _ in range(max(1, min(int(limit), 1024))):
            if not self._library.SDL_PollEvent(ctypes.byref(event)):
                break
            event_type = int(event.type)
            if event_type == SDL_EVENT_GAMEPAD_ADDED:
                identifier = int(event.gdevice.which)
                self._open(identifier)
                results.append(GamepadEvent("connected", identifier))
            elif event_type == SDL_EVENT_GAMEPAD_REMOVED:
                identifier = int(event.gdevice.which)
                self._remove(identifier)
                results.append(GamepadEvent("disconnected", identifier))
            elif event_type == SDL_EVENT_GAMEPAD_REMAPPED:
                identifier = int(event.gdevice.which)
                self._remove(identifier)
                self._open(identifier)
                results.append(GamepadEvent("remapped", identifier))
            elif event_type in {SDL_EVENT_GAMEPAD_BUTTON_DOWN, SDL_EVENT_GAMEPAD_BUTTON_UP}:
                value = event.gbutton
                results.append(GamepadEvent("button", int(value.which), _BUTTON_NAMES.get(int(value.button), f"button_{int(value.button)}"), event_type == SDL_EVENT_GAMEPAD_BUTTON_DOWN, 1.0 if event_type == SDL_EVENT_GAMEPAD_BUTTON_DOWN else 0.0, float(value.timestamp) / 1_000_000_000.0))
            elif event_type == SDL_EVENT_GAMEPAD_AXIS_MOTION:
                value = event.gaxis
                normalized = max(-1.0, min(1.0, float(value.value) / 32767.0))
                results.append(GamepadEvent("axis", int(value.which), _AXIS_NAMES.get(int(value.axis), f"axis_{int(value.axis)}"), abs(normalized) > 0.0, normalized, float(value.timestamp) / 1_000_000_000.0))
        return tuple(results)

    def list_devices(self) -> Sequence[GamepadDevice]:
        return tuple(self._devices.values())

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for identifier in tuple(self._handles):
            self._remove(identifier)
        if self._started:
            self._library.SDL_QuitSubSystem(SDL_INIT_GAMEPAD)
            self._started = False


class UnavailableGamepadProvider:
    def __init__(self, reason: str = "SDL3 not installed") -> None:
        self.reason = reason
    @property
    def available(self) -> bool:
        return False
    @property
    def status(self) -> str:
        return "Missing"
    def start(self) -> Sequence[GamepadDevice]:
        return ()
    def poll_events(self, limit: int = 256) -> Sequence[GamepadEvent]:
        return ()
    def list_devices(self) -> Sequence[GamepadDevice]:
        return ()
    def close(self) -> None:
        return None


class FakeGamepadProvider:
    """In-memory provider used by tests; it never accesses host input devices."""
    def __init__(self, devices: Sequence[GamepadDevice] = (), *, available: bool = True) -> None:
        self._devices = {device.instance_id: device for device in devices}
        self._events: deque[GamepadEvent] = deque()
        self._available = available
        self.closed = False
    @property
    def available(self) -> bool:
        return self._available
    @property
    def status(self) -> str:
        return "Available" if self._available else "Missing"
    def start(self) -> Sequence[GamepadDevice]:
        return self.list_devices()
    def emit(self, event: GamepadEvent, device: GamepadDevice | None = None) -> None:
        if event.kind == "connected" and device is not None:
            self._devices[device.instance_id] = device
        elif event.kind == "remapped" and device is not None:
            self._devices[device.instance_id] = device
        elif event.kind == "disconnected":
            self._devices.pop(event.instance_id, None)
        self._events.append(event)
    def poll_events(self, limit: int = 256) -> Sequence[GamepadEvent]:
        events = []
        for _ in range(min(max(0, limit), len(self._events))):
            events.append(self._events.popleft())
        return tuple(events)
    def list_devices(self) -> Sequence[GamepadDevice]:
        return tuple(self._devices.values())
    def close(self) -> None:
        self.closed = True
        self._events.clear()


def create_gamepad_provider(*, mapping_file: Path | None = None) -> GamepadProvider:
    try:
        return SDL3GamepadProvider(mapping_file=mapping_file)
    except (SDL3Unavailable, OSError, AttributeError) as error:
        logger.info("SDL3 gamepad support unavailable: %s", error)
        return UnavailableGamepadProvider(str(error))


__all__ = ["FakeGamepadProvider", "GamepadProvider", "SDL3GamepadProvider", "SDL3Unavailable", "UnavailableGamepadProvider", "create_gamepad_provider"]
