"""EmptySpaceItem – blue free-time block between travel end and next visit."""

from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QPen, QColor, QFont
from PySide6.QtWidgets import QGraphicsObject

from domain.models import EmptySpace
from domain.constants import (
    VISIT_WIDTH, EMPTY_HEIGHT,
    COLOR_EMPTY_BG, COLOR_EMPTY_BORDER,
)

_PAD = 6


class EmptySpaceItem(QGraphicsObject):
    """Blue block showing unused time in a route."""

    def __init__(self, space: EmptySpace, font_size: int = 12, parent=None):
        super().__init__(parent)
        self._space = space
        self._font_size = font_size
        self.setCacheMode(QGraphicsObject.CacheMode.DeviceCoordinateCache)

    @property
    def space(self) -> EmptySpace:
        return self._space

    def set_font_size(self, size: int):
        self._font_size = size
        self.prepareGeometryChange()
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
        painter.setPen(QPen(QColor(COLOR_EMPTY_BORDER), 1))
        painter.drawRect(1, 1, w - 2, h - 2)

        painter.setFont(QFont("Segoe UI", max(fs - 3, 7)))
        painter.setPen(QColor("#0D47A1"))
        label = (f"{self._space.start_time} – {self._space.end_time}  "
                 f"({self._space.duration_minutes} min ledig)")
        painter.drawText(QRectF(_PAD, 0, w - _PAD * 2, h),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         label)
