"""Central gamepad action mapping and Qt-safe SDL polling."""

from __future__ import annotations

import logging
import time
from typing import Sequence

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from ..config import GAMEPAD_MAPPINGS_FILE
from ..models import (
    GamepadAction,
    GamepadDevice,
    GamepadEvent,
    GamepadType,
    button_hints,
)
from ..providers.gamepad import GamepadProvider, create_gamepad_provider


logger = logging.getLogger(__name__)
_NAVIGATION_ACTIONS = {
    GamepadAction.NAVIGATE_UP,
    GamepadAction.NAVIGATE_DOWN,
    GamepadAction.NAVIGATE_LEFT,
    GamepadAction.NAVIGATE_RIGHT,
}


class GamepadInputMapper:
    """Translate raw controls to stable UI actions with repeat and dead-zone logic."""

    def __init__(
        self,
        *,
        deadzone: float = 0.20,
        repeat_delay_ms: int = 350,
        repeat_rate_ms: int = 110,
        swap_accept_back: bool = False,
        debounce_ms: int = 80,
    ) -> None:
        self.configure(
            deadzone=deadzone,
            repeat_delay_ms=repeat_delay_ms,
            repeat_rate_ms=repeat_rate_ms,
            swap_accept_back=swap_accept_back,
        )
        self.debounce_seconds = max(0.0, debounce_ms / 1000.0)
        self._held: dict[str, tuple[GamepadAction, float]] = {}
        self._axis_controls: dict[str, GamepadAction] = {}
        self._last_press: dict[tuple[int, str], float] = {}
        self._view_holds: dict[int, tuple[float, bool]] = {}
        self.long_press_seconds = 2.0

    def configure(
        self,
        *,
        deadzone: float,
        repeat_delay_ms: int,
        repeat_rate_ms: int,
        swap_accept_back: bool,
    ) -> None:
        self.deadzone = min(0.75, max(0.05, float(deadzone)))
        self.repeat_delay_seconds = min(1.5, max(0.15, int(repeat_delay_ms) / 1000.0))
        self.repeat_rate_seconds = min(0.5, max(0.05, int(repeat_rate_ms) / 1000.0))
        self.swap_accept_back = bool(swap_accept_back)

    def reset(self, instance_id: int | None = None) -> None:
        if instance_id is None:
            self._held.clear()
            self._axis_controls.clear()
            self._last_press.clear()
            self._view_holds.clear()
            return
        prefix = f"{instance_id}:"
        self._held = {key: value for key, value in self._held.items() if not key.startswith(prefix)}
        self._axis_controls = {key: value for key, value in self._axis_controls.items() if not key.startswith(prefix)}
        self._last_press = {key: value for key, value in self._last_press.items() if key[0] != instance_id}
        self._view_holds.pop(instance_id, None)

    @staticmethod
    def _button_action(control: str) -> GamepadAction | None:
        return {
            "south": GamepadAction.CONFIRM,
            "east": GamepadAction.BACK,
            "west": GamepadAction.CONTEXT_MENU,
            "north": GamepadAction.CONTEXT_MENU,
            "start": GamepadAction.OPEN_SYSTEM_MENU,
            "guide": GamepadAction.TOGGLE_DESKTOP_COUCH,
            "left_shoulder": GamepadAction.PAGE_LEFT,
            "right_shoulder": GamepadAction.PAGE_RIGHT,
            "dpad_up": GamepadAction.NAVIGATE_UP,
            "dpad_down": GamepadAction.NAVIGATE_DOWN,
            "dpad_left": GamepadAction.NAVIGATE_LEFT,
            "dpad_right": GamepadAction.NAVIGATE_RIGHT,
        }.get(control)

    def _swap(self, action: GamepadAction) -> GamepadAction:
        if not self.swap_accept_back:
            return action
        if action is GamepadAction.CONFIRM:
            return GamepadAction.BACK
        if action is GamepadAction.BACK:
            return GamepadAction.CONFIRM
        return action

    def process(self, event: GamepadEvent, *, now: float | None = None) -> tuple[GamepadAction, ...]:
        timestamp = time.monotonic() if now is None else float(now)
        if event.kind == "button":
            return self._process_button(event, timestamp)
        if event.kind == "axis" and event.control in {"left_x", "left_y"}:
            return self._process_axis(event, timestamp)
        if event.kind == "disconnected":
            self.reset(event.instance_id)
        return ()

    def _process_button(self, event: GamepadEvent, now: float) -> tuple[GamepadAction, ...]:
        if event.control == "back":
            if event.pressed:
                self._view_holds[event.instance_id] = (now, False)
                return ()
            started, emitted = self._view_holds.pop(
                event.instance_id, (now, False)
            )
            if emitted or now - started >= self.long_press_seconds:
                return ()
            return (GamepadAction.CONTEXT_MENU,)
        action = self._button_action(event.control)
        if action is None:
            return ()
        action = self._swap(action)
        control_key = f"{event.instance_id}:button:{event.control}"
        if not event.pressed:
            self._held.pop(control_key, None)
            return ()
        debounce_key = (event.instance_id, event.control)
        if now - self._last_press.get(debounce_key, -1e9) < self.debounce_seconds:
            return ()
        self._last_press[debounce_key] = now
        if action in _NAVIGATION_ACTIONS:
            self._held[control_key] = (action, now + self.repeat_delay_seconds)
        return (action,)

    def _process_axis(self, event: GamepadEvent, now: float) -> tuple[GamepadAction, ...]:
        control_key = f"{event.instance_id}:axis:{event.control}"
        previous = self._axis_controls.get(control_key)
        if abs(event.value) <= self.deadzone:
            self._axis_controls.pop(control_key, None)
            self._held.pop(control_key, None)
            return ()
        if event.control == "left_x":
            action = GamepadAction.NAVIGATE_RIGHT if event.value > 0 else GamepadAction.NAVIGATE_LEFT
        else:
            action = GamepadAction.NAVIGATE_DOWN if event.value > 0 else GamepadAction.NAVIGATE_UP
        if action is previous:
            return ()
        self._axis_controls[control_key] = action
        self._held[control_key] = (action, now + self.repeat_delay_seconds)
        return (action,)

    def poll_repeats(self, *, now: float | None = None) -> tuple[GamepadAction, ...]:
        timestamp = time.monotonic() if now is None else float(now)
        actions: list[GamepadAction] = []
        for instance_id, (started, emitted) in tuple(self._view_holds.items()):
            if not emitted and timestamp - started >= self.long_press_seconds:
                actions.append(GamepadAction.OPEN_SYSTEM_MENU)
                self._view_holds[instance_id] = (started, True)
        for control, (action, due) in tuple(self._held.items()):
            if timestamp < due:
                continue
            actions.append(action)
            self._held[control] = (action, timestamp + self.repeat_rate_seconds)
        return tuple(actions)


class GamepadService(QObject):
    availabilityChanged = Signal()
    controllersChanged = Signal()
    activeControllerChanged = Signal()
    actionTriggered = Signal(str)
    controllerConnected = Signal(object)
    controllerDisconnected = Signal(str)
    mappingChanged = Signal(object)
    inputActivity = Signal(str)

    def __init__(self, provider: GamepadProvider | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._provider = provider or create_gamepad_provider(
            mapping_file=GAMEPAD_MAPPINGS_FILE
        )
        self._available = bool(self._provider.available)
        self._status = str(self._provider.status)
        self._devices: list[GamepadDevice] = []
        self._active_id: int | None = None
        self._mapper = GamepadInputMapper()
        self._started = False
        self._stopped = False
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self.pollNow)

    @Property(bool, notify=availabilityChanged)
    def available(self) -> bool:
        return bool(self._available)

    @Property(str, notify=availabilityChanged)
    def status(self) -> str:
        return self._status

    @Property("QVariantList", notify=controllersChanged)
    def controllers(self) -> list[dict[str, object]]:
        return [device.to_dict() for device in self._devices]

    @Property(int, notify=controllersChanged)
    def controllerCount(self) -> int:
        return len(self._devices)

    @Property("QVariantMap", notify=activeControllerChanged)
    def activeController(self) -> dict[str, object]:
        device = self._device(self._active_id)
        return device.to_dict() if device else {}

    @Property("QVariantMap", notify=activeControllerChanged)
    def buttonHints(self) -> dict[str, str]:
        device = self._device(self._active_id)
        return button_hints(device.gamepad_type if device else GamepadType.GENERIC)

    def configure(
        self,
        *,
        deadzone: float,
        repeat_delay_ms: int,
        repeat_rate_ms: int,
        swap_accept_back: bool,
    ) -> None:
        self._mapper.configure(
            deadzone=deadzone,
            repeat_delay_ms=repeat_delay_ms,
            repeat_rate_ms=repeat_rate_ms,
            swap_accept_back=swap_accept_back,
        )

    def start(self) -> None:
        if self._started or self._stopped:
            return
        try:
            self._devices = list(self._provider.start())
            self._available = bool(self._provider.available)
            self._status = str(self._provider.status)
        except Exception as error:
            logger.warning("Gamepad provider could not start: %s", error)
            self._devices = []
            self._available = False
            self._status = "Missing"
        self._started = True
        self.availabilityChanged.emit()
        self.controllersChanged.emit()
        if self._available:
            self._timer.start()

    @Slot()
    def pollNow(self) -> None:
        if self._stopped:
            return
        try:
            events = tuple(self._provider.poll_events(256))
        except Exception as error:
            logger.warning("SDL3 event polling failed: %s", error)
            self._timer.stop()
            self._available = False
            self._status = "Error"
            self.availabilityChanged.emit()
            return
        devices_changed = False
        for event in events:
            if event.kind in {"connected", "disconnected", "remapped"}:
                previous = self._device(event.instance_id)
                self._devices = list(self._provider.list_devices())
                devices_changed = True
                current = self._device(event.instance_id)
                if event.kind == "connected" and current:
                    self.controllerConnected.emit(current.to_dict())
                elif event.kind == "disconnected":
                    self._mapper.reset(event.instance_id)
                    self.controllerDisconnected.emit(previous.name if previous else str(event.instance_id))
                    if self._active_id == event.instance_id:
                        self._active_id = None
                        self.activeControllerChanged.emit()
                elif event.kind == "remapped" and current:
                    self.mappingChanged.emit(current.to_dict())
            meaningful = event.kind in {"button", "axis"} and (
                event.pressed or abs(event.value) > self._mapper.deadzone
            )
            actions = self._mapper.process(event)
            if meaningful:
                self._set_active(event.instance_id)
                self.inputActivity.emit(actions[0].value if actions else "")
            for action in actions:
                self.actionTriggered.emit(action.value)
        for action in self._mapper.poll_repeats():
            self.actionTriggered.emit(action.value)
        if devices_changed:
            self.controllersChanged.emit()

    def _device(self, identifier: int | None) -> GamepadDevice | None:
        if identifier is None:
            return None
        return next((device for device in self._devices if device.instance_id == identifier), None)

    def _set_active(self, identifier: int) -> None:
        if self._device(identifier) is None or self._active_id == identifier:
            return
        self._active_id = identifier
        self.activeControllerChanged.emit()

    @Slot()
    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._timer.stop()
        self._mapper.reset()
        try:
            self._provider.close()
        except Exception:
            logger.exception("Could not close the gamepad provider cleanly")


__all__ = ["GamepadInputMapper", "GamepadService"]
