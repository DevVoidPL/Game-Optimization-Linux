"""Native freeze-frame selector for Narrator subtitle regions."""

from __future__ import annotations

import base64
import binascii
from enum import StrEnum
from typing import Any, Mapping

from PySide6.QtCore import (
    QCoreApplication,
    QObject,
    QPointF,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QCloseEvent,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QPen,
    QShowEvent,
    QWindow,
)
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from game_optimization_linux.models.narrator import NormalizedRect
from game_optimization_linux.services.narrator_region import fitted_preview_content


def _tr(text: str) -> str:
    return QCoreApplication.translate("SubtitleRegionSelector", text)


class RegionInteractionMode(StrEnum):
    NONE = "none"
    CREATE = "create"
    MOVE = "move"
    NORTH = "north"
    SOUTH = "south"
    EAST = "east"
    WEST = "west"
    NORTHEAST = "northeast"
    NORTHWEST = "northwest"
    SOUTHEAST = "southeast"
    SOUTHWEST = "southwest"


class RegionCanvas(QWidget):
    """Paint one frame and own all physical pointer interaction."""

    selectionChanged = Signal(object)
    cancelRequested = Signal()

    HANDLE_RADIUS = 8.0
    MIN_SELECTION_DIP = 2.0

    def __init__(
        self,
        image: QImage,
        *,
        source_width: int,
        source_height: int,
        initial_region: NormalizedRect | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if image.isNull():
            raise ValueError("The captured game frame is empty")
        if source_width <= 0 or source_height <= 0:
            raise ValueError("The captured frame dimensions must be positive")
        self.setObjectName("narratorRegionCanvas")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.setMinimumSize(320, 180)
        self._image = image.copy()
        self._source_width = int(source_width)
        self._source_height = int(source_height)
        self._normalized_region: QRectF | None = (
            QRectF(
                initial_region.x,
                initial_region.y,
                initial_region.width,
                initial_region.height,
            )
            if initial_region is not None
            else None
        )
        self._interaction_mode = RegionInteractionMode.NONE
        self._press_position = QPointF()
        self._base_selection = QRectF()

    @property
    def source_width(self) -> int:
        return self._source_width

    @property
    def source_height(self) -> int:
        return self._source_height

    @property
    def interaction_mode(self) -> RegionInteractionMode:
        return self._interaction_mode

    def fitted_image_rect(self) -> QRectF:
        if self.width() <= 0 or self.height() <= 0:
            return QRectF()
        content = fitted_preview_content(
            self._source_width,
            self._source_height,
            float(self.width()),
            float(self.height()),
        )
        return QRectF(content.x, content.y, content.width, content.height)

    def selection_rect(self) -> QRectF:
        fitted = self.fitted_image_rect()
        region = self._normalized_region
        if fitted.isEmpty() or region is None:
            return QRectF()
        return QRectF(
            fitted.left() + region.left() * fitted.width(),
            fitted.top() + region.top() * fitted.height(),
            region.width() * fitted.width(),
            region.height() * fitted.height(),
        )

    def normalized_region(self) -> NormalizedRect | None:
        region = self._normalized_region
        if region is None or not self.has_valid_selection():
            return None
        x = max(0.0, min(1.0, region.left()))
        y = max(0.0, min(1.0, region.top()))
        width = min(max(0.0, region.width()), 1.0 - x)
        height = min(max(0.0, region.height()), 1.0 - y)
        if width <= 0 or height <= 0:
            return None
        return NormalizedRect(x=x, y=y, width=width, height=height)

    def selected_source_size(self) -> tuple[int, int]:
        region = self.normalized_region()
        if region is None:
            return (0, 0)
        return (
            max(1, round(region.width * self._source_width)),
            max(1, round(region.height * self._source_height)),
        )

    def has_valid_selection(self) -> bool:
        region = self._normalized_region
        if region is None:
            return False
        return (
            region.width() * self._source_width >= 1.0
            and region.height() * self._source_height >= 1.0
        )

    def reset_selection(self) -> None:
        self._interaction_mode = RegionInteractionMode.NONE
        self._normalized_region = None
        self.selectionChanged.emit(None)
        self.update()

    def set_normalized_region(self, region: NormalizedRect | None) -> None:
        self._normalized_region = (
            QRectF(region.x, region.y, region.width, region.height)
            if region is not None
            else None
        )
        self.selectionChanged.emit(
            region.to_dict() if region is not None else None
        )
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.fillRect(self.rect(), QColor("#05070a"))
        fitted = self.fitted_image_rect()
        if fitted.isEmpty():
            return
        painter.drawImage(fitted, self._image)
        selection = self.selection_rect()
        if selection.isEmpty():
            return

        shade = QColor(0, 0, 0, 155)
        painter.fillRect(
            QRectF(fitted.left(), fitted.top(), fitted.width(), selection.top() - fitted.top()),
            shade,
        )
        painter.fillRect(
            QRectF(
                fitted.left(),
                selection.bottom(),
                fitted.width(),
                fitted.bottom() - selection.bottom(),
            ),
            shade,
        )
        painter.fillRect(
            QRectF(fitted.left(), selection.top(), selection.left() - fitted.left(), selection.height()),
            shade,
        )
        painter.fillRect(
            QRectF(
                selection.right(),
                selection.top(),
                fitted.right() - selection.right(),
                selection.height(),
            ),
            shade,
        )
        accent = QColor("#52a8ff")
        painter.setPen(QPen(accent, 2.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(selection)
        painter.setBrush(QColor("#f5f7fa"))
        for point in self._handle_points(selection):
            painter.drawRect(
                QRectF(
                    point.x() - 4.0,
                    point.y() - 4.0,
                    8.0,
                    8.0,
                )
            )

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() is not Qt.MouseButton.LeftButton:
            event.ignore()
            return
        point = event.position()
        fitted = self.fitted_image_rect()
        if fitted.isEmpty() or not fitted.contains(point):
            event.ignore()
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self._press_position = point
        selection = self.selection_rect()
        mode = self._hit_test(point, selection)
        if mode is RegionInteractionMode.NONE:
            mode = RegionInteractionMode.CREATE
            selection = QRectF(point, point)
        self._interaction_mode = mode
        self._base_selection = QRectF(selection)
        if mode is RegionInteractionMode.CREATE:
            self._set_selection_from_canvas_rect(selection)
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._interaction_mode is RegionInteractionMode.NONE:
            self.setCursor(self._cursor_for_point(event.position()))
            event.ignore()
            return
        self._update_interaction(event.position())
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            event.button() is not Qt.MouseButton.LeftButton
            or self._interaction_mode is RegionInteractionMode.NONE
        ):
            event.ignore()
            return
        self._update_interaction(event.position())
        self._interaction_mode = RegionInteractionMode.NONE
        self.unsetCursor()
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.cancelRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    @staticmethod
    def _handle_points(rect: QRectF) -> tuple[QPointF, ...]:
        return (
            rect.topLeft(),
            QPointF(rect.center().x(), rect.top()),
            rect.topRight(),
            QPointF(rect.left(), rect.center().y()),
            QPointF(rect.right(), rect.center().y()),
            rect.bottomLeft(),
            QPointF(rect.center().x(), rect.bottom()),
            rect.bottomRight(),
        )

    def _hit_test(
        self,
        point: QPointF,
        selection: QRectF,
    ) -> RegionInteractionMode:
        if selection.isEmpty():
            return RegionInteractionMode.NONE
        radius = self.HANDLE_RADIUS
        near_left = abs(point.x() - selection.left()) <= radius
        near_right = abs(point.x() - selection.right()) <= radius
        near_top = abs(point.y() - selection.top()) <= radius
        near_bottom = abs(point.y() - selection.bottom()) <= radius
        within_x = selection.left() - radius <= point.x() <= selection.right() + radius
        within_y = selection.top() - radius <= point.y() <= selection.bottom() + radius
        if near_left and near_top:
            return RegionInteractionMode.NORTHWEST
        if near_right and near_top:
            return RegionInteractionMode.NORTHEAST
        if near_left and near_bottom:
            return RegionInteractionMode.SOUTHWEST
        if near_right and near_bottom:
            return RegionInteractionMode.SOUTHEAST
        if near_left and within_y:
            return RegionInteractionMode.WEST
        if near_right and within_y:
            return RegionInteractionMode.EAST
        if near_top and within_x:
            return RegionInteractionMode.NORTH
        if near_bottom and within_x:
            return RegionInteractionMode.SOUTH
        if selection.contains(point):
            return RegionInteractionMode.MOVE
        return RegionInteractionMode.NONE

    def _cursor_for_point(self, point: QPointF) -> Qt.CursorShape:
        mode = self._hit_test(point, self.selection_rect())
        if mode in {RegionInteractionMode.NORTH, RegionInteractionMode.SOUTH}:
            return Qt.CursorShape.SizeVerCursor
        if mode in {RegionInteractionMode.EAST, RegionInteractionMode.WEST}:
            return Qt.CursorShape.SizeHorCursor
        if mode in {
            RegionInteractionMode.NORTHWEST,
            RegionInteractionMode.SOUTHEAST,
        }:
            return Qt.CursorShape.SizeFDiagCursor
        if mode in {
            RegionInteractionMode.NORTHEAST,
            RegionInteractionMode.SOUTHWEST,
        }:
            return Qt.CursorShape.SizeBDiagCursor
        if mode is RegionInteractionMode.MOVE:
            return Qt.CursorShape.SizeAllCursor
        return Qt.CursorShape.CrossCursor

    def _update_interaction(self, raw_point: QPointF) -> None:
        fitted = self.fitted_image_rect()
        point = QPointF(
            max(fitted.left(), min(fitted.right(), raw_point.x())),
            max(fitted.top(), min(fitted.bottom(), raw_point.y())),
        )
        base = QRectF(self._base_selection)
        mode = self._interaction_mode
        if mode is RegionInteractionMode.CREATE:
            rect = QRectF(self._press_position, point).normalized()
        elif mode is RegionInteractionMode.MOVE:
            delta = point - self._press_position
            left = max(
                fitted.left(),
                min(fitted.right() - base.width(), base.left() + delta.x()),
            )
            top = max(
                fitted.top(),
                min(fitted.bottom() - base.height(), base.top() + delta.y()),
            )
            rect = QRectF(left, top, base.width(), base.height())
        else:
            left, top, right, bottom = (
                base.left(),
                base.top(),
                base.right(),
                base.bottom(),
            )
            if "west" in mode.value:
                left = min(point.x(), right - self.MIN_SELECTION_DIP)
            if "east" in mode.value:
                right = max(point.x(), left + self.MIN_SELECTION_DIP)
            if "north" in mode.value:
                top = min(point.y(), bottom - self.MIN_SELECTION_DIP)
            if "south" in mode.value:
                bottom = max(point.y(), top + self.MIN_SELECTION_DIP)
            rect = QRectF(QPointF(left, top), QPointF(right, bottom)).normalized()
        self._set_selection_from_canvas_rect(rect.intersected(fitted))

    def _set_selection_from_canvas_rect(self, rect: QRectF) -> None:
        fitted = self.fitted_image_rect()
        if fitted.isEmpty() or rect.isEmpty():
            self._normalized_region = None
            self.selectionChanged.emit(None)
            self.update()
            return
        clipped = rect.intersected(fitted)
        self._normalized_region = QRectF(
            max(0.0, min(1.0, (clipped.left() - fitted.left()) / fitted.width())),
            max(0.0, min(1.0, (clipped.top() - fitted.top()) / fitted.height())),
            max(0.0, min(1.0, clipped.width() / fitted.width())),
            max(0.0, min(1.0, clipped.height() / fitted.height())),
        )
        region = self.normalized_region()
        self.selectionChanged.emit(region.to_dict() if region is not None else None)
        self.update()


class SubtitleRegionSelectorWindow(QWidget):
    """Frameless top-level selector containing a direct-event canvas."""

    selectionSubmitted = Signal(object)
    cancelled = Signal()
    shown = Signal()

    def __init__(
        self,
        image: QImage,
        *,
        source_width: int,
        source_height: int,
        initial_region: NormalizedRect | None = None,
        parent: QWidget | None = None,
    ) -> None:
        flags = (
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        super().__init__(parent, flags)
        self.setObjectName("nativeSubtitleRegionSelector")
        self.setWindowTitle(_tr("Select subtitle area"))
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setStyleSheet(
            "QWidget { background: #10151d; color: #f5f7fa; }"
            "QLabel#selectorHint { color: #aeb8c5; }"
            "QPushButton { padding: 7px 16px; border-radius: 5px;"
            " border: 1px solid #536173; background: #1b2430; }"
            "QPushButton:hover { background: #263344; }"
            "QPushButton:disabled { color: #687383; border-color: #384250; }"
            "QPushButton#saveRegionButton { background: #2f82d4;"
            " border-color: #52a8ff; color: white; }"
        )
        self._completion_requested = False
        self.canvas = RegionCanvas(
            image,
            source_width=source_width,
            source_height=source_height,
            initial_region=initial_region,
            parent=self,
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)
        instruction = QLabel(_tr("Select subtitle area"), self)
        instruction.setObjectName("selectorInstruction")
        instruction.setStyleSheet("font-size: 18px; font-weight: 600;")
        hint = QLabel(
            _tr(
                "Drag over subtitles. Drag the selection to move it or use its edges and corners to resize it."
            ),
            self,
        )
        hint.setObjectName("selectorHint")
        hint.setWordWrap(True)
        layout.addWidget(instruction)
        layout.addWidget(hint)
        layout.addWidget(self.canvas, 1)

        footer = QHBoxLayout()
        self._dimensions_label = QLabel(self)
        self._dimensions_label.setObjectName("selectorDimensions")
        footer.addWidget(self._dimensions_label, 1)
        self.reset_button = QPushButton(_tr("Reset"), self)
        self.reset_button.setObjectName("resetRegionButton")
        self.cancel_button = QPushButton(_tr("Cancel"), self)
        self.cancel_button.setObjectName("cancelRegionButton")
        self.save_button = QPushButton(_tr("Save region"), self)
        self.save_button.setObjectName("saveRegionButton")
        footer.addWidget(self.reset_button)
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.save_button)
        layout.addLayout(footer)

        self.canvas.selectionChanged.connect(self._selection_changed)
        self.canvas.cancelRequested.connect(self.request_cancel)
        self.reset_button.clicked.connect(self.canvas.reset_selection)
        self.cancel_button.clicked.connect(self.request_cancel)
        self.save_button.clicked.connect(self.submit_selection)
        self._selection_changed(
            initial_region.to_dict() if initial_region is not None else None
        )

    def set_error(self, message: str) -> None:
        self._dimensions_label.setText(str(message))
        self._dimensions_label.setStyleSheet("color: #ff6978;")

    def submit_selection(self) -> None:
        region = self.canvas.normalized_region()
        if region is None:
            return
        self.selectionSubmitted.emit(region.to_dict())

    def request_cancel(self) -> None:
        if self._completion_requested:
            return
        self._completion_requested = True
        self.cancelled.emit()

    def mark_completed(self) -> None:
        self._completion_requested = True

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.request_cancel()
            event.accept()
            return
        super().keyPressEvent(event)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self.canvas.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        self.shown.emit()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if not self._completion_requested:
            self._completion_requested = True
            self.cancelled.emit()
        event.accept()

    def _selection_changed(self, _region: object) -> None:
        valid = self.canvas.has_valid_selection()
        self.save_button.setEnabled(valid)
        width, height = self.canvas.selected_source_size()
        self._dimensions_label.setStyleSheet("")
        if valid:
            self._dimensions_label.setText(
                _tr("Source: %1x%2    OCR region: %3x%4")
                .replace("%1", str(self.canvas.source_width))
                .replace("%2", str(self.canvas.source_height))
                .replace("%3", str(width))
                .replace("%4", str(height))
            )
        else:
            self._dimensions_label.setText(
                _tr("Source: %1x%2    OCR region: %3x%4")
                .replace("%1", str(self.canvas.source_width))
                .replace("%2", str(self.canvas.source_height))
                .replace("%3", "0")
                .replace("%4", "0")
            )


class NarratorRegionSelectorCoordinator(QObject):
    """Own the native selector and restore the QML window around it."""

    selectionSubmitted = Signal(str, object)
    cancelled = Signal(str)
    opened = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._main_window: QWindow | None = None
        self._window: SubtitleRegionSelectorWindow | None = None
        self._game_id = ""
        self._main_was_visible = False
        self._main_visibility: QWindow.Visibility | None = None
        self._main_hidden_by_selector = False

    @property
    def active_window(self) -> SubtitleRegionSelectorWindow | None:
        return self._window

    def attach_main_window(self, window: QObject | None) -> None:
        self._main_window = window if isinstance(window, QWindow) else None

    def show_selector(
        self,
        game_id: str,
        preview: Mapping[str, Any],
        initial_region: Mapping[str, Any] | None,
    ) -> None:
        application = QApplication.instance()
        if not isinstance(application, QApplication):
            raise RuntimeError("The native subtitle selector requires QApplication")
        if self._window is not None:
            self.close_selector(cancelled=False)
        image = self._image_from_data_url(str(preview.get("imageUrl", "")))
        source_width = int(preview.get("sourceWidth", 0))
        source_height = int(preview.get("sourceHeight", 0))
        region = NormalizedRect.from_dict(initial_region)
        window = SubtitleRegionSelectorWindow(
            image,
            source_width=source_width,
            source_height=source_height,
            initial_region=region,
        )
        self._window = window
        self._game_id = str(game_id)
        self._remember_main_window_state()
        window.selectionSubmitted.connect(self._selection_submitted)
        window.cancelled.connect(self._cancelled)
        window.shown.connect(self._selector_shown)
        window.winId()
        handle = window.windowHandle()
        if handle is not None:
            if self._main_window is not None:
                handle.setTransientParent(self._main_window)
                screen = self._main_window.screen()
                if screen is not None:
                    handle.setScreen(screen)
            elif application.primaryScreen() is not None:
                handle.setScreen(application.primaryScreen())
        window.showFullScreen()
        window.activateWindow()

    def complete_selection(self) -> None:
        self.close_selector(cancelled=False)

    def show_error(self, message: str) -> None:
        if self._window is not None:
            self._window.set_error(message)

    def close_selector(self, *, cancelled: bool) -> None:
        window = self._window
        game_id = self._game_id
        if window is None:
            return
        self._window = None
        self._game_id = ""
        window.mark_completed()
        self._restore_main_window_state()
        window.close()
        window.deleteLater()
        if cancelled:
            self.cancelled.emit(game_id)

    def shutdown(self) -> None:
        self.close_selector(cancelled=False)

    def _selector_shown(self) -> None:
        game_id = self._game_id
        QTimer.singleShot(0, self._hide_main_window_after_show)
        self.opened.emit(game_id)

    def _selection_submitted(self, region: object) -> None:
        self.selectionSubmitted.emit(self._game_id, region)

    def _cancelled(self) -> None:
        self.close_selector(cancelled=True)

    def _remember_main_window_state(self) -> None:
        main = self._main_window
        self._main_hidden_by_selector = False
        if main is None:
            self._main_was_visible = False
            self._main_visibility = None
            return
        try:
            self._main_was_visible = main.isVisible()
            self._main_visibility = main.visibility()
        except RuntimeError:
            self._main_window = None
            self._main_was_visible = False
            self._main_visibility = None

    def _hide_main_window_after_show(self) -> None:
        window = self._window
        main = self._main_window
        if window is None or not window.isVisible() or main is None:
            return
        try:
            if main.isVisible():
                main.hide()
                self._main_hidden_by_selector = True
        except RuntimeError:
            self._main_window = None

    def _restore_main_window_state(self) -> None:
        main = self._main_window
        if main is None:
            return
        try:
            if self._main_was_visible:
                visibility = self._main_visibility
                if visibility is None or visibility is QWindow.Visibility.Hidden:
                    main.show()
                else:
                    main.setVisibility(visibility)
                main.requestActivate()
            elif self._main_hidden_by_selector:
                main.hide()
        except RuntimeError:
            self._main_window = None
        finally:
            self._main_hidden_by_selector = False
            self._main_visibility = None

    @staticmethod
    def _image_from_data_url(data_url: str) -> QImage:
        prefix, separator, payload = data_url.partition(",")
        if not separator or not prefix.casefold().startswith("data:image/png;base64"):
            raise ValueError("The subtitle preview is not a PNG data URL")
        try:
            encoded = base64.b64decode(payload, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("The subtitle preview PNG is malformed") from error
        image = QImage.fromData(encoded, "PNG")
        if image.isNull():
            raise ValueError("The subtitle preview PNG could not be decoded")
        return image


__all__ = [
    "NarratorRegionSelectorCoordinator",
    "RegionCanvas",
    "RegionInteractionMode",
    "SubtitleRegionSelectorWindow",
]
