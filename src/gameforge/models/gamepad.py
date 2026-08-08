"""Controller-neutral gamepad values shared by providers, services, and QML."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class GamepadType(str, Enum):
    XBOX = "Xbox"
    PLAYSTATION = "PlayStation"
    NINTENDO = "Nintendo"
    STEAM = "Steam Controller"
    STEAM_DECK = "Steam Deck"
    GENERIC = "Generic"
    UNKNOWN = "Unknown"


class GamepadAction(str, Enum):
    NAVIGATE_UP = "NavigateUp"
    NAVIGATE_DOWN = "NavigateDown"
    NAVIGATE_LEFT = "NavigateLeft"
    NAVIGATE_RIGHT = "NavigateRight"
    CONFIRM = "Confirm"
    BACK = "Back"
    CONTEXT_MENU = "ContextMenu"
    PAGE_LEFT = "PageLeft"
    PAGE_RIGHT = "PageRight"
    OPEN_SYSTEM_MENU = "OpenSystemMenu"
    TOGGLE_DESKTOP_COUCH = "ToggleDesktopCouch"
    # Compatibility values accepted by older third-party QML integrations.
    ACCEPT = "Accept"
    OPEN_MENU = "OpenMenu"
    SEARCH = "Search"
    PREVIOUS_SECTION = "PreviousSection"
    NEXT_SECTION = "NextSection"
    TOGGLE_MODE = "ToggleMode"


@dataclass(frozen=True, slots=True)
class GamepadDevice:
    instance_id: int
    name: str
    gamepad_type: GamepadType = GamepadType.UNKNOWN
    mapping_status: str = "Mapped"
    battery_percent: int | None = None
    connected: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.instance_id),
            "instanceId": self.instance_id,
            "name": self.name,
            "type": self.gamepad_type.value,
            "mappingStatus": self.mapping_status,
            "batteryPercent": self.battery_percent,
            "batteryAvailable": self.battery_percent is not None,
            "connected": bool(self.connected),
        }


@dataclass(frozen=True, slots=True)
class GamepadEvent:
    kind: str
    instance_id: int
    control: str = ""
    pressed: bool = False
    value: float = 0.0
    timestamp: float = 0.0


def button_hints(gamepad_type: GamepadType) -> dict[str, str]:
    if gamepad_type is GamepadType.PLAYSTATION:
        return {"accept": "Cross", "confirm": "Cross", "back": "Circle", "menu": "Options", "systemMenu": "Options", "search": "Triangle", "context": "Triangle", "previous": "L1", "next": "R1", "pageLeft": "L1", "pageRight": "R1"}
    if gamepad_type is GamepadType.NINTENDO:
        return {"accept": "A", "confirm": "A", "back": "B", "menu": "+", "systemMenu": "+", "search": "X", "context": "X", "previous": "L", "next": "R", "pageLeft": "L", "pageRight": "R"}
    if gamepad_type in {GamepadType.STEAM, GamepadType.STEAM_DECK}:
        return {"accept": "A", "confirm": "A", "back": "B", "menu": "Menu", "systemMenu": "Menu", "search": "Y", "context": "Y", "previous": "L1", "next": "R1", "pageLeft": "L1", "pageRight": "R1"}
    if gamepad_type is GamepadType.XBOX:
        return {"accept": "A", "confirm": "A", "back": "B", "menu": "Menu", "systemMenu": "Menu", "search": "Y", "context": "Y", "previous": "LB", "next": "RB", "pageLeft": "LB", "pageRight": "RB"}
    return {"accept": "South", "confirm": "South", "back": "East", "menu": "Start", "systemMenu": "Start", "search": "North", "context": "North", "previous": "L1", "next": "R1", "pageLeft": "L1", "pageRight": "R1"}
