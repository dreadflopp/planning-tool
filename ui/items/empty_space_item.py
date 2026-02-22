"""EmptySpaceItem – blue free-time block between travel end and next visit."""

from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QPainter, QPen, QColor, QFont
from PySide6.QtWidgets import QGraphicsObject, QGraphicsSceneMouseEvent

from domain.models import EmptySpace
from domain.constants import (
    VISIT_WIDTH, EMPTY_HEIGHT,
    COLOR_EMPTY_BG, COLOR_EMPTY_BORDER,
)

_PAD = 6


class EmptySpaceItem(QGraphicsObject):
    """Blue block showing unused time in a route."""

    remove_requested = Signal(object)

    def __init__(self, space: EmptySpace, font_size: int = 12, parent=None):
        super().__init__(parent)
        self._space = space
        self._font_size = font_size
        self._inconsistent = False
        self.setCacheMode(QGraphicsObject.CacheMode.DeviceCoordinateCache)

    @property
    def space(self) -> EmptySpace:
        return self._space

    def set_font_size(self, size: int):
        self._font_size = size
        self.prepareGeometryChange()
        self.update()

    def set_inconsistent(self, inconsistent: bool):
        if self._inconsistent != inconsistent:
            self._inconsistent = inconsistent
            self.update()

    def height(self) -> int:
        if self._space.duration_minutes == 0:
            return 0
        from controllers.route_layout_engine import _scaled
        return _scaled(EMPTY_HEIGHT, self._font_size)

    def width(self) -> int:
        from controllers.route_layout_engine import _scaled
        return _scaled(VISIT_WIDTH, self._font_size)

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self.width(), self.height())

    def paint(self, painter: QPainter, option, widget=None):
        if self._space.duration_minutes == 0:
            return
        w, h = self.width(), self.height()
        fs = self._font_size

        painter.fillRect(0, 0, w, h, QColor(COLOR_EMPTY_BG))
        border_pen = QPen(QColor("#C62828"), 2) if self._inconsistent else QPen(QColor(COLOR_EMPTY_BORDER), 1)
        painter.setPen(border_pen)
        painter.drawRect(1, 1, w - 2, h - 2)

        painter.setFont(QFont("Segoe UI", max(fs - 3, 7)))
        painter.setPen(QColor("#0D47A1"))
        label = f"Lucka: {self._space.duration_minutes} min"
        painter.drawText(QRectF(_PAD, 0, w - 44, h),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         label)

        remove_rect = QRectF(w - 34, 4, 28, h - 8)
        painter.setPen(QPen(QColor("#0D47A1"), 1))
        painter.drawRoundedRect(remove_rect, 3, 3)
        painter.setFont(QFont("Segoe UI", max(fs - 2, 8), QFont.Weight.Bold))
        painter.drawText(remove_rect, Qt.AlignmentFlag.AlignCenter, "✕")

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent):
        event.accept()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            w, h = self.width(), self.height()
            pos = event.pos()
            remove_rect = QRectF(w - 34, 4, 28, h - 8)
            if remove_rect.contains(pos):
                self.remove_requested.emit(self)
                event.accept()
                return
        super().mouseReleaseEvent(event)
