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
    """Controller actions shared by Python and QML.

    The first fifteen values are the production semantic contract. Values below
    the compatibility marker are accepted only at legacy integration boundaries;
    physical input never emits them.
    """

    NAVIGATE_UP = "NavigateUp"
    NAVIGATE_DOWN = "NavigateDown"
    NAVIGATE_LEFT = "NavigateLeft"
    NAVIGATE_RIGHT = "NavigateRight"
    CONFIRM = "Confirm"
    BACK = "Back"
    SECONDARY_ACTION = "SecondaryAction"
    MORE_ACTIONS = "MoreActions"
    PREVIOUS_TAB = "PreviousTab"
    NEXT_TAB = "NextTab"
    PAGE_UP = "PageUp"
    PAGE_DOWN = "PageDown"
    OPEN_SYSTEM_MENU = "OpenSystemMenu"
    CONTEXT_ACTION_1 = "ContextAction1"
    CONTEXT_ACTION_2 = "ContextAction2"

    # Compatibility values accepted by existing probes and integrations.
    ACCEPT = "Accept"
    OPEN_MENU = "OpenMenu"
    SEARCH = "Search"
    CONTEXT_MENU = "ContextMenu"
    PAGE_LEFT = "PageLeft"
    PAGE_RIGHT = "PageRight"
    PREVIOUS_SECTION = "PreviousSection"
    NEXT_SECTION = "NextSection"
    TOGGLE_MODE = "ToggleMode"
    TOGGLE_DESKTOP_COUCH = "ToggleDesktopCouch"


CANONICAL_GAMEPAD_ACTIONS = frozenset(
    {
        GamepadAction.NAVIGATE_UP,
        GamepadAction.NAVIGATE_DOWN,
        GamepadAction.NAVIGATE_LEFT,
        GamepadAction.NAVIGATE_RIGHT,
        GamepadAction.CONFIRM,
        GamepadAction.BACK,
        GamepadAction.SECONDARY_ACTION,
        GamepadAction.MORE_ACTIONS,
        GamepadAction.PREVIOUS_TAB,
        GamepadAction.NEXT_TAB,
        GamepadAction.PAGE_UP,
        GamepadAction.PAGE_DOWN,
        GamepadAction.OPEN_SYSTEM_MENU,
        GamepadAction.CONTEXT_ACTION_1,
        GamepadAction.CONTEXT_ACTION_2,
    }
)

_ACTION_ALIASES = {
    GamepadAction.ACCEPT: GamepadAction.CONFIRM,
    GamepadAction.OPEN_MENU: GamepadAction.OPEN_SYSTEM_MENU,
    GamepadAction.SEARCH: GamepadAction.MORE_ACTIONS,
    GamepadAction.CONTEXT_MENU: GamepadAction.MORE_ACTIONS,
    GamepadAction.PAGE_LEFT: GamepadAction.PREVIOUS_TAB,
    GamepadAction.PREVIOUS_SECTION: GamepadAction.PREVIOUS_TAB,
    GamepadAction.PAGE_RIGHT: GamepadAction.NEXT_TAB,
    GamepadAction.NEXT_SECTION: GamepadAction.NEXT_TAB,
}


def normalize_gamepad_action(value: object) -> GamepadAction | None:
    """Return one canonical action, or ``None`` for unknown/system commands."""

    try:
        action = value if isinstance(value, GamepadAction) else GamepadAction(str(value).strip())
    except ValueError:
        return None
    normalized = _ACTION_ALIASES.get(action, action)
    return normalized if normalized in CANONICAL_GAMEPAD_ACTIONS else None


@dataclass(frozen=True, slots=True)
class GamepadDevice:
    instance_id: int
    name: str
    gamepad_type: GamepadType = GamepadType.UNKNOWN
    mapping_status: str = "Mapped"
    battery_percent: int | None = None
    connected: bool = True
    guid: str = ""
    vendor_id: int | None = None
    product_id: int | None = None

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
            "guid": self.guid,
            "vendorId": self.vendor_id,
            "productId": self.product_id,
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
    """Return text-only, redistributable labels for the active controller."""

    if gamepad_type is GamepadType.PLAYSTATION:
        values = {
            "confirm": "Cross", "back": "Circle", "secondary": "Square",
            "more": "Triangle", "systemMenu": "Options", "context1": "Create",
            "context2": "R3", "previousTab": "L1", "nextTab": "R1",
            "pageUp": "L2", "pageDown": "R2",
        }
    elif gamepad_type is GamepadType.NINTENDO:
        values = {
            "confirm": "A", "back": "B", "secondary": "Y", "more": "X",
            "systemMenu": "+", "context1": "-", "context2": "R Stick",
            "previousTab": "L", "nextTab": "R", "pageUp": "ZL", "pageDown": "ZR",
        }
    elif gamepad_type in {GamepadType.STEAM, GamepadType.STEAM_DECK}:
        values = {
            "confirm": "A", "back": "B", "secondary": "X", "more": "Y",
            "systemMenu": "Menu", "context1": "View", "context2": "R3",
            "previousTab": "L1", "nextTab": "R1", "pageUp": "L2", "pageDown": "R2",
        }
    elif gamepad_type is GamepadType.XBOX:
        values = {
            "confirm": "A", "back": "B", "secondary": "X", "more": "Y",
            "systemMenu": "Menu", "context1": "View", "context2": "R3",
            "previousTab": "LB", "nextTab": "RB", "pageUp": "LT", "pageDown": "RT",
        }
    else:
        values = {
            "confirm": "South", "back": "East", "secondary": "West", "more": "North",
            "systemMenu": "Start", "context1": "Select", "context2": "R3",
            "previousTab": "L1", "nextTab": "R1", "pageUp": "L2", "pageDown": "R2",
        }

    # Keep legacy consumers working while all production QML moves to semantic keys.
    values.update({
        "accept": values["confirm"],
        "menu": values["systemMenu"],
        "search": values["more"],
        "context": values["more"],
        "previous": values["previousTab"],
        "next": values["nextTab"],
        "pageLeft": values["previousTab"],
        "pageRight": values["nextTab"],
    })
    return values
