"""VisitItem – QGraphicsObject that renders one care visit block."""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from PySide6.QtCore import Qt, QRectF, Signal, QPointF, QMimeData
from PySide6.QtGui import (
    QPainter, QPen, QColor, QFont, QBrush, QDrag, QPixmap, QPainterPath,
)
from PySide6.QtWidgets import QGraphicsObject, QGraphicsSceneMouseEvent, QInputDialog

from domain.models import RouteEntry, Visit, VisitColor
from domain.constants import (
    VISIT_WIDTH, VISIT_HEIGHT,
    COLOR_VISIT_BG, COLOR_VISIT_BORDER,
    COLOR_VISIT_GREEN, COLOR_VISIT_PINK, COLOR_VISIT_BLUE,
    COLOR_PAIR_HIGHLIGHT, COLOR_GREYED_OUT,
    MIME_ROUTE_ENTRY, MIME_POOL_VISIT,
)

if TYPE_CHECKING:
    pass

_COLOR_HEX = {
    VisitColor.GREEN: COLOR_VISIT_GREEN,
    VisitColor.PINK: COLOR_VISIT_PINK,
    VisitColor.BLUE: COLOR_VISIT_BLUE,
}

_COLOR_STRIP_W = 6
_PAD = 6
_COL2_W = 52
_COL3_W = 22


class VisitItem(QGraphicsObject):
    """
    Renders a visit as a fixed-height rectangular block.

    In pool mode  → read-only, no arrows, drag initiates pool DnD.
    In route mode → editable times/duration, up/down position arrows, drag re-orders.
    """

    move_up_requested = Signal(object)    # emits self
    move_down_requested = Signal(object)
    time_edit_requested = Signal(object)  # emits self; caller opens dialog
    duration_up_requested = Signal(object)
    duration_down_requested = Signal(object)
    selected = Signal(object)             # emits self on any left click

    def __init__(self, entry: RouteEntry, font_size: int = 12,
                 in_route: bool = True, parent=None):
        super().__init__(parent)
        self._entry = entry
        self._font_size = font_size
        self._in_route = in_route
        self._greyed_out = False
        self._highlight_pair = False
        self._selected = False
        self._drag_start: Optional[QPointF] = None
        self.setAcceptHoverEvents(True)
        self.setCacheMode(QGraphicsObject.CacheMode.DeviceCoordinateCache)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def entry(self) -> RouteEntry:
        return self._entry

    def set_font_size(self, size: int):
        self._font_size = size
        self.prepareGeometryChange()
        self.update()

    def set_greyed_out(self, greyed: bool):
        if self._greyed_out != greyed:
            self._greyed_out = greyed
            self.update()

    def set_pair_highlight(self, highlighted: bool):
        if self._highlight_pair != highlighted:
            self._highlight_pair = highlighted
            self.update()

    def set_selected(self, selected: bool):
        if self._selected != selected:
            self._selected = selected
            self.update()

    def height(self) -> int:
        from controllers.route_layout_engine import _scaled
        return _scaled(VISIT_HEIGHT, self._font_size)

    def width(self) -> int:
        from controllers.route_layout_engine import _scaled
        return _scaled(VISIT_WIDTH, self._font_size)

    # ------------------------------------------------------------------
    # QGraphicsItem interface
    # ------------------------------------------------------------------

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self.width(), self.height())

    def paint(self, painter: QPainter, option, widget=None):
        w, h = self.width(), self.height()
        fs = self._font_size

        # Background
        if self._selected:
            bg = QColor("#E3F2FD")
        elif self._greyed_out:
            bg = QColor(COLOR_GREYED_OUT)
        else:
            bg = QColor(COLOR_VISIT_BG)
        painter.fillRect(0, 0, w, h, bg)

        # Color strip (left edge)
        color_key = self._entry.display_color
        if color_key and not self._greyed_out:
            strip_color = QColor(_COLOR_HEX.get(color_key, COLOR_VISIT_BG))
            painter.fillRect(0, 0, _COLOR_STRIP_W, h, strip_color)

        # Border: selection > pair highlight > normal
        if self._selected:
            pen = QPen(QColor("#1565C0"), 3)
        elif self._highlight_pair:
            pen = QPen(QColor(COLOR_PAIR_HIGHLIGHT), 3)
        else:
            pen = QPen(QColor(COLOR_VISIT_BORDER), 1)
        painter.setPen(pen)
        painter.drawRect(1, 1, w - 2, h - 2)

        text_color = QColor("#888888" if self._greyed_out else "#212121")

        col1_x = _COLOR_STRIP_W + _PAD
        col2_x = w - (_COL2_W + _COL3_W) - _PAD if self._in_route else w - _COL2_W - _PAD
        col3_x = w - _COL3_W - 2

        # --- Column 1 ---
        name_font = QFont("Segoe UI", fs, QFont.Weight.Bold)
        painter.setFont(name_font)
        painter.setPen(text_color)
        name_rect = QRectF(col1_x, _PAD, col2_x - col1_x - _PAD, h * 0.28)
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         self._entry.display_name)

        small_font = QFont("Segoe UI", max(fs - 2, 7))
        painter.setFont(small_font)

        time_str = f"{self._entry.start_time} – {self._entry.end_time}"
        time_rect = QRectF(col1_x, h * 0.28, col2_x - col1_x - _PAD, h * 0.22)
        painter.drawText(time_rect,
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         time_str)

        addr_rect = QRectF(col1_x, h * 0.50, col2_x - col1_x - _PAD, h * 0.22)
        painter.drawText(addr_rect,
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         self._entry.display_address)

        ins = self._entry.display_insatser
        if ins:
            ins_font = QFont("Segoe UI", max(fs - 3, 7))
            painter.setFont(ins_font)
            ins_rect = QRectF(col1_x, h * 0.74, w - col1_x - _PAD, h * 0.22)
            painter.drawText(ins_rect,
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             ins)

        if not self._greyed_out:
            # --- Column 2: duration ---
            from domain.models import TravelMode as _TM  # noqa: avoid circular at module level
            start_m = self._hm_to_min(self._entry.start_time)
            end_m = self._hm_to_min(self._entry.end_time)
            dur = max(0, end_m - start_m)

            dur_font = QFont("Segoe UI", fs + 1, QFont.Weight.Bold)
            painter.setFont(dur_font)
            painter.setPen(QColor("#1565C0"))
            dur_rect = QRectF(col2_x, _PAD, _COL2_W, h * 0.5)
            painter.drawText(dur_rect,
                             Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter,
                             f"{dur}")

            if self._in_route:
                # Duration arrows ▲▼
                arrow_font = QFont("Segoe UI", max(fs - 3, 7))
                painter.setFont(arrow_font)
                painter.setPen(QColor("#1565C0"))
                up_rect = QRectF(col2_x, h * 0.50, _COL2_W, h * 0.24)
                dn_rect = QRectF(col2_x, h * 0.74, _COL2_W, h * 0.24)
                painter.drawText(up_rect, Qt.AlignmentFlag.AlignCenter, "▲")
                painter.drawText(dn_rect, Qt.AlignmentFlag.AlignCenter, "▼")

                # --- Column 3: position arrows ---
                painter.setPen(QColor("#555555"))
                pos_up_rect = QRectF(col3_x, _PAD, _COL3_W, h * 0.5 - _PAD)
                pos_dn_rect = QRectF(col3_x, h * 0.5, _COL3_W, h * 0.5 - _PAD)
                painter.setFont(arrow_font)
                painter.drawText(pos_up_rect, Qt.AlignmentFlag.AlignCenter, "↑")
                painter.drawText(pos_dn_rect, Qt.AlignmentFlag.AlignCenter, "↓")

    # ------------------------------------------------------------------
    # Mouse events
    # ------------------------------------------------------------------

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.pos()
            self.selected.emit(self)
        event.accept()  # must accept so mouseReleaseEvent is delivered

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent):
        if (self._drag_start is not None and
                (event.pos() - self._drag_start).manhattanLength() > 10):
            self._start_drag(event)
            self._drag_start = None
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent):
        self._drag_start = None
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.pos()
            w, h = self.width(), self.height()
            if self._in_route:
                col2_x = w - (_COL2_W + _COL3_W) - _PAD
                col3_x = w - _COL3_W - 2

                # Duration up arrow
                if (col2_x <= pos.x() <= col2_x + _COL2_W and
                        h * 0.50 <= pos.y() <= h * 0.74):
                    self.duration_up_requested.emit(self)
                    return
                # Duration down arrow
                if (col2_x <= pos.x() <= col2_x + _COL2_W and
                        h * 0.74 <= pos.y() <= h):
                    self.duration_down_requested.emit(self)
                    return
                # Position up
                if pos.x() >= col3_x and pos.y() < h * 0.5:
                    self.move_up_requested.emit(self)
                    return
                # Position down
                if pos.x() >= col3_x and pos.y() >= h * 0.5:
                    self.move_down_requested.emit(self)
                    return
                # Time range click (row 2)
                col1_x = _COLOR_STRIP_W + _PAD
                if (col1_x <= pos.x() <= col2_x and
                        h * 0.28 <= pos.y() <= h * 0.50):
                    self.time_edit_requested.emit(self)
                    return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QGraphicsSceneMouseEvent):
        if self._in_route:
            self.time_edit_requested.emit(self)
        super().mouseDoubleClickEvent(event)

    # ------------------------------------------------------------------
    # Drag
    # ------------------------------------------------------------------

    def _start_drag(self, event: QGraphicsSceneMouseEvent):
        import json as _json
        from PySide6.QtWidgets import QGraphicsRectItem

        # Show ghost placeholder in original position while dragging
        scene = self.scene()
        ghost = None
        if scene:
            ghost = QGraphicsRectItem(QRectF(0, 0, self.width(), self.height()))
            ghost.setPos(self.mapToScene(QPointF(0, 0)))
            ghost.setBrush(QBrush(QColor(255, 255, 255, 200)))
            ghost.setPen(QPen(QColor(180, 180, 180, 180), 1, Qt.PenStyle.DashLine))
            ghost.setZValue(10)
            scene.addItem(ghost)

        drag = QDrag(event.widget())
        mime = QMimeData()
        if self._in_route:
            mime.setData(MIME_ROUTE_ENTRY,
                         _json.dumps({"entry_id": self._entry.id,
                                      "route_id": self._entry.route_id}).encode())
        else:
            mime.setData(MIME_POOL_VISIT,
                         _json.dumps({"visit_id": self._entry.visit_id}).encode())
        drag.setMimeData(mime)

        # Pixmap that follows the cursor
        pix = QPixmap(self.width(), self.height())
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        self.paint(p, None)
        p.end()
        drag.setPixmap(pix)
        drag.setHotSpot(event.pos().toPoint())
        drag.exec(Qt.DropAction.MoveAction)

        # Remove ghost after drag completes
        if ghost and ghost.scene():
            ghost.scene().removeItem(ghost)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _hm_to_min(hhmm: str) -> int:
        try:
            h, m = hhmm.split(":")
            return int(h) * 60 + int(m)
        except Exception:
            return 0
