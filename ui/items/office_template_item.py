"""OfficeTemplateItem – fixed draggable default-template visits in the pool."""

from __future__ import annotations

import re

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QDrag, QPixmap
from PySide6.QtWidgets import QGraphicsObject, QGraphicsSceneMouseEvent

from domain.constants import (
    VISIT_WIDTH, VISIT_HEIGHT, MIME_OFFICE_TEMPLATE,
)
from PySide6.QtCore import QMimeData

_PAD = 6


class OfficeTemplateItem(QGraphicsObject):
    """
    Fixed item at the top of the pool panel.
    Drag it to a route to create a default office-type visit instance.
    """

    def __init__(self, name: str, full_address: str, duration_minutes: int,
                 font_size: int = 12, parent=None):
        super().__init__(parent)
        self._name = name
        self._full_address = full_address
        self._duration = max(1, duration_minutes)
        self._font_size = font_size
        self._drag_start: QPointF | None = None
        self.setAcceptHoverEvents(True)

    @staticmethod
    def _display_address(full_address: str) -> str:
        return re.sub(r"\s*\d{3}\s?\d{2}\s+\S.*$", "", full_address).strip()

    def width(self) -> int:
        from controllers.route_layout_engine import _scaled
        return _scaled(VISIT_WIDTH, self._font_size)

    def height(self) -> int:
        from controllers.route_layout_engine import _scaled
        return _scaled(VISIT_HEIGHT, self._font_size)

    def set_font_size(self, size: int):
        self._font_size = size
        self.prepareGeometryChange()
        self.update()

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self.width(), self.height())

    def _name_rect(self) -> QRectF:
        w, h = self.width(), self.height()
        return QRectF(_PAD, _PAD, w - _PAD * 2, h * 0.45)

    def _addr_rect(self) -> QRectF:
        w, h = self.width(), self.height()
        return QRectF(_PAD, h * 0.45, w - _PAD * 2, h * 0.45)

    def paint(self, painter: QPainter, option, widget=None):
        w, h = self.width(), self.height()
        fs = self._font_size

        painter.fillRect(0, 0, w, h, QColor("#FAFAFA"))
        painter.fillRect(0, 0, 6, h, QColor("#212121"))
        painter.setPen(QPen(QColor("#9E9E9E"), 1))
        painter.drawRect(1, 1, w - 2, h - 2)

        # Name row
        painter.setFont(QFont("Segoe UI", fs, QFont.Weight.Bold))
        painter.setPen(QColor("#212121"))
        painter.drawText(
            self._name_rect(),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._name,
        )

        # Address row
        painter.setFont(QFont("Segoe UI", max(fs - 2, 7)))
        painter.setPen(QColor("#424242"))
        painter.drawText(
            self._addr_rect(),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._display_address(self._full_address),
        )

        # Duration (shown before placement)
        painter.setFont(QFont("Segoe UI", fs + 1, QFont.Weight.Bold))
        painter.setPen(QColor("#1565C0"))
        painter.drawText(
            QRectF(w - 54, 0, 52, h),
            Qt.AlignmentFlag.AlignCenter,
            f"{self._duration}",
        )

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.pos()
        event.accept()

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent):
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
        import json
        drag = QDrag(event.widget())
        mime = QMimeData()
        mime.setData(
            MIME_OFFICE_TEMPLATE,
            json.dumps({
                "name": self._name,
                "address": self._full_address,
                "duration_minutes": self._duration,
            }).encode(),
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
