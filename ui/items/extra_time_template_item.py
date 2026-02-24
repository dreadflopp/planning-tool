"""ExtraTimeTemplateItem – draggable pool template for extra-time blocks."""

from __future__ import annotations

import json

from PySide6.QtCore import Qt, QRectF, QPointF, QMimeData
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QDrag, QPixmap
from PySide6.QtWidgets import QGraphicsObject, QGraphicsSceneMouseEvent

from domain.constants import VISIT_WIDTH, EMPTY_HEIGHT, MIME_EXTRA_TIME_TEMPLATE

_PAD = 6


class ExtraTimeTemplateItem(QGraphicsObject):
    def __init__(self, duration_minutes: int, font_size: int = 12, parent=None):
        super().__init__(parent)
        self._duration = max(0, int(duration_minutes))
        self._font_size = font_size
        self._drag_start: QPointF | None = None
        self.setAcceptHoverEvents(True)

    def set_duration_minutes(self, minutes: int):
        self._duration = max(0, int(minutes))
        self.setVisible(self._duration > 0)
        self.update()

    def set_font_size(self, size: int):
        self._font_size = size
        self.prepareGeometryChange()
        self.update()

    def width(self) -> int:
        from controllers.route_layout_engine import _scaled
        return _scaled(VISIT_WIDTH, self._font_size)

    def height(self) -> int:
        from controllers.route_layout_engine import _scaled
        return _scaled(EMPTY_HEIGHT, self._font_size)

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self.width(), self.height())

    def paint(self, painter: QPainter, option, widget=None):
        w, h = self.width(), self.height()
        fs = self._font_size

        painter.fillRect(0, 0, w, h, QColor("#BBDEFB"))
        painter.setPen(QPen(QColor("#64B5F6"), 1))
        painter.drawRect(1, 1, w - 2, h - 2)

        painter.setFont(QFont("Segoe UI", max(fs - 3, 7)))
        painter.setPen(QColor("#0D47A1"))
        painter.drawText(
            QRectF(_PAD, 0, w - _PAD * 2, h),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            f"Extratid: {self._duration} min",
        )

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.pos()
        event.accept()

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent):
        if self._duration <= 0:
            return
        if (self._drag_start is not None and
                (event.pos() - self._drag_start).manhattanLength() > 10):
            self._start_drag(event)
            self._drag_start = None
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent):
        self._drag_start = None
        super().mouseReleaseEvent(event)

    def _start_drag(self, event: QGraphicsSceneMouseEvent):
        drag = QDrag(event.widget())
        mime = QMimeData()
        mime.setData(
            MIME_EXTRA_TIME_TEMPLATE,
            json.dumps({"duration_minutes": self._duration}).encode(),
        )
        drag.setMimeData(mime)

        pix = QPixmap(self.width(), self.height())
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        self.paint(p, None)
        p.end()
        drag.setPixmap(pix)
        drag.setHotSpot(event.pos().toPoint())
        drag.exec(Qt.DropAction.CopyAction)
