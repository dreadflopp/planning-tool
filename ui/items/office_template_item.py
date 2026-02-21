"""OfficeTemplateItem – the reusable office/template visit in the pool."""

from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QDrag, QPixmap
from PySide6.QtWidgets import QGraphicsObject, QGraphicsSceneMouseEvent, QInputDialog

from domain.models import OfficeTemplate
from domain.constants import (
    VISIT_WIDTH, OFFICE_TEMPLATE_HEIGHT, MIME_OFFICE_TEMPLATE,
)
from PySide6.QtCore import QMimeData

_PAD = 6


class OfficeTemplateItem(QGraphicsObject):
    """
    Fixed item at the top of the pool panel.
    Drag it to a route to create an office visit instance.
    Double-click the name line to edit name, double-click the address line to edit address.
    """

    template_changed = Signal()  # emitted after any edit so main window can persist

    def __init__(self, template: OfficeTemplate, font_size: int = 12, parent=None):
        super().__init__(parent)
        self._template = template
        self._font_size = font_size
        self._drag_start: QPointF | None = None
        self.setAcceptHoverEvents(True)

    @property
    def template(self) -> OfficeTemplate:
        return self._template

    def width(self) -> int:
        from controllers.route_layout_engine import _scaled
        return _scaled(VISIT_WIDTH, self._font_size)

    def height(self) -> int:
        from controllers.route_layout_engine import _scaled
        return _scaled(OFFICE_TEMPLATE_HEIGHT, self._font_size)

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

        painter.fillRect(0, 0, w, h, QColor("#FFF3E0"))
        painter.setPen(QPen(QColor("#FF8F00"), 2))
        painter.drawRect(1, 1, w - 2, h - 2)

        # Hint: double-click to edit
        painter.setFont(QFont("Segoe UI", max(fs - 4, 6)))
        painter.setPen(QColor("#FFCC80"))
        painter.drawText(
            QRectF(w - 120, 2, 116, 12),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            "dubbelklick för att redigera",
        )

        # Name row
        painter.setFont(QFont("Segoe UI", fs, QFont.Weight.Bold))
        painter.setPen(QColor("#E65100"))
        painter.drawText(
            self._name_rect(),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            f"📋  {self._template.name}",
        )

        # Address row
        painter.setFont(QFont("Segoe UI", max(fs - 2, 7)))
        painter.setPen(QColor("#BF360C"))
        painter.drawText(
            self._addr_rect(),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._template.address or "  (dubbelklicka för att ange adress)",
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

    def mouseDoubleClickEvent(self, event: QGraphicsSceneMouseEvent):
        pos = event.pos()
        if self._name_rect().contains(pos):
            self._edit_name()
        elif self._addr_rect().contains(pos):
            self._edit_address()
        event.accept()

    def _edit_name(self):
        text, ok = QInputDialog.getText(
            None, "Redigera namn", "Namn på mallbesöket:",
            text=self._template.name,
        )
        if ok and text.strip():
            self._template.name = text.strip()
            self.template_changed.emit()
            self.update()

    def _edit_address(self):
        text, ok = QInputDialog.getText(
            None, "Redigera adress", "Adress för mallbesöket:",
            text=self._template.address,
        )
        if ok:
            self._template.address = text.strip()
            self.template_changed.emit()
            self.update()

    def _start_drag(self, event: QGraphicsSceneMouseEvent):
        import json
        drag = QDrag(event.widget())
        mime = QMimeData()
        mime.setData(
            MIME_OFFICE_TEMPLATE,
            json.dumps({
                "name": self._template.name,
                "address": self._template.address,
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
