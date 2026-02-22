"""ExtraTimeItem – green extra-time block inserted before a visit."""

from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QPainter, QPen, QColor, QFont
from PySide6.QtWidgets import QGraphicsObject, QGraphicsSceneMouseEvent

from domain.models import ExtraTimeBlock
from domain.constants import VISIT_WIDTH, EMPTY_HEIGHT

_PAD = 6


class ExtraTimeItem(QGraphicsObject):
    """Green block showing global extra-time minutes for one target entry."""

    remove_requested = Signal(object)

    def __init__(self, block: ExtraTimeBlock, duration_minutes: int,
                 font_size: int = 12, parent=None):
        super().__init__(parent)
        self._block = block
        self._duration_minutes = max(0, int(duration_minutes))
        self._font_size = font_size
        self.setCacheMode(QGraphicsObject.CacheMode.DeviceCoordinateCache)

    @property
    def block(self) -> ExtraTimeBlock:
        return self._block

    def set_duration_minutes(self, minutes: int):
        self._duration_minutes = max(0, int(minutes))
        self.update()

    def set_font_size(self, size: int):
        self._font_size = size
        self.prepareGeometryChange()
        self.update()

    def height(self) -> int:
        if self._duration_minutes == 0:
            return 0
        from controllers.route_layout_engine import _scaled
        return _scaled(EMPTY_HEIGHT, self._font_size)

    def width(self) -> int:
        from controllers.route_layout_engine import _scaled
        return _scaled(VISIT_WIDTH, self._font_size)

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self.width(), self.height())

    def paint(self, painter: QPainter, option, widget=None):
        if self._duration_minutes == 0:
            return
        w, h = self.width(), self.height()
        fs = self._font_size

        painter.fillRect(0, 0, w, h, QColor("#E8F5E9"))
        painter.setPen(QPen(QColor("#2E7D32"), 1))
        painter.drawRect(1, 1, w - 2, h - 2)

        painter.setFont(QFont("Segoe UI", max(fs - 3, 7)))
        painter.setPen(QColor("#1B5E20"))
        label = f"Extratid: {self._duration_minutes} min"
        painter.drawText(QRectF(_PAD, 0, w - 44, h),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         label)

        remove_rect = QRectF(w - 34, 4, 28, h - 8)
        painter.setPen(QPen(QColor("#1B5E20"), 1))
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
