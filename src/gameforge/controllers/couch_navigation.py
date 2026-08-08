"""Central focus and modal state for the controller-driven Couch interface."""

from __future__ import annotations

from typing import Any, Sequence

from PySide6.QtCore import QObject, Property, Signal, Slot


class CouchNavigationController(QObject):
    """Remember stable focus IDs without owning any business data."""

    activeScreenChanged = Signal()
    focusedIdChanged = Signal()
    modalChanged = Signal()
    inputBlockedChanged = Signal()
    controllerConnectedChanged = Signal()
    actionRequested = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._active_screen = "home"
        self._focused_id = ""
        self._remembered: dict[str, str] = {}
        self._indices: dict[str, int] = {}
        self._history: list[str] = []
        self._modal_id = ""
        self._modal_return: tuple[str, str] | None = None
        self._input_blocked = False
        self._controller_connected = True

    @Property(str, notify=activeScreenChanged)
    def activeScreen(self) -> str:
        return self._active_screen

    @Property(str, notify=focusedIdChanged)
    def focusedId(self) -> str:
        return self._focused_id

    @Property(str, notify=modalChanged)
    def modalId(self) -> str:
        return self._modal_id

    @Property(bool, notify=modalChanged)
    def modalOpen(self) -> bool:
        return bool(self._modal_id)

    @Property(bool, notify=inputBlockedChanged)
    def inputBlocked(self) -> bool:
        return self._input_blocked

    @Property(bool, notify=controllerConnectedChanged)
    def controllerConnected(self) -> bool:
        return self._controller_connected

    @Slot(str, str)
    def enterScreen(self, screen: str, preferred_id: str = "") -> None:
        normalized = str(screen).strip() or "home"
        if normalized != self._active_screen:
            if not self._history or self._history[-1] != self._active_screen:
                self._history.append(self._active_screen)
                self._history = self._history[-32:]
            self._active_screen = normalized
            self.activeScreenChanged.emit()
        target = str(preferred_id).strip() or self._remembered.get(normalized, "")
        self._set_focus(target)

    @Slot(result=str)
    def previousScreen(self) -> str:
        if not self._history:
            return "home"
        previous = self._history.pop()
        self._active_screen = previous
        self.activeScreenChanged.emit()
        self._set_focus(self._remembered.get(previous, ""))
        return previous

    @Slot(str, str, int)
    def rememberFocus(self, screen: str, item_id: str, index: int = -1) -> None:
        normalized_screen = str(screen).strip() or self._active_screen
        normalized_id = str(item_id).strip()
        if normalized_id:
            self._remembered[normalized_screen] = normalized_id
        if index >= 0:
            self._indices[normalized_screen] = int(index)
        if normalized_screen == self._active_screen:
            self._set_focus(normalized_id)

    @Slot(str, "QVariantList", result=str)
    def reconcileFocus(self, screen: str, available_ids: Sequence[Any]) -> str:
        normalized_screen = str(screen).strip() or self._active_screen
        values = [str(value).strip() for value in available_ids if str(value).strip()]
        if not values:
            self._remembered.pop(normalized_screen, None)
            self._indices[normalized_screen] = 0
            if normalized_screen == self._active_screen:
                self._set_focus("")
            return ""
        remembered = self._remembered.get(normalized_screen, "")
        if remembered in values:
            selected = remembered
        else:
            index = max(0, min(self._indices.get(normalized_screen, 0), len(values) - 1))
            selected = values[index]
        self._remembered[normalized_screen] = selected
        self._indices[normalized_screen] = values.index(selected)
        if normalized_screen == self._active_screen:
            self._set_focus(selected)
        return selected

    @Slot(str, str)
    def openModal(self, modal_id: str, safe_focus_id: str = "cancel") -> None:
        if not self._modal_id:
            self._modal_return = (self._active_screen, self._focused_id)
        self._modal_id = str(modal_id).strip() or "modal"
        self._set_focus(str(safe_focus_id).strip() or "cancel")
        self.modalChanged.emit()

    @Slot()
    def closeModal(self) -> None:
        if not self._modal_id:
            return
        self._modal_id = ""
        restore = self._modal_return
        self._modal_return = None
        if restore is not None:
            screen, focus_id = restore
            if screen != self._active_screen:
                self._active_screen = screen
                self.activeScreenChanged.emit()
            self._set_focus(focus_id)
        self.modalChanged.emit()

    @Slot(bool)
    def setInputBlocked(self, blocked: bool) -> None:
        normalized = bool(blocked)
        if normalized == self._input_blocked:
            return
        self._input_blocked = normalized
        self.inputBlockedChanged.emit()

    @Slot(bool)
    def setControllerConnected(self, connected: bool) -> None:
        normalized = bool(connected)
        if normalized == self._controller_connected:
            return
        self._controller_connected = normalized
        self.controllerConnectedChanged.emit()

    @Slot(str, result=bool)
    def dispatch(self, action: str) -> bool:
        normalized = str(action).strip()
        if not normalized or self._input_blocked or not self._controller_connected:
            return False
        self.actionRequested.emit(normalized)
        return True

    def _set_focus(self, item_id: str) -> None:
        normalized = str(item_id).strip()
        if normalized == self._focused_id:
            return
        self._focused_id = normalized
        self.focusedIdChanged.emit()


__all__ = ["CouchNavigationController"]
