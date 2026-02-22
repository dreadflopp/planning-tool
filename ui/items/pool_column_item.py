"""PoolColumnItem – one street column in the visit pool panel."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import QPainter, QPen, QColor, QFont
from PySide6.QtWidgets import QGraphicsObject, QGraphicsSceneMouseEvent

from domain.models import Visit, RouteEntry
from domain.constants import (
    VISIT_WIDTH, HEADER_HEIGHT, VISIT_HEIGHT,
    COLOR_POOL_COLUMN_BG, COLOR_HEADER_BG,
)
from ui.items.visit_item import VisitItem

_BTN_W = 26
_BTN_H = 22
_PAD = 6


def _entry_from_visit(visit: Visit, font_size: int) -> RouteEntry:
    """Wrap a pool Visit in a temporary RouteEntry for display."""
    return RouteEntry(
        id=None, route_id=-1, visit_id=visit.id,
        position=0, start_time=visit.default_start, end_time=visit.default_end,
        visit=visit,
    )


class PoolColumnItem(QGraphicsObject):
    """
    Renders a street group column in the visit pool.
    Visits are read-only, sorted by default_start.
    """

    move_left_requested = Signal(object)    # emits self
    move_right_requested = Signal(object)
    visit_selected = Signal(object, object)  # (column_item, visit_item)

    def __init__(self, street: str, visits: list[Visit],
                 font_size: int = 12, parent=None):
        super().__init__(parent)
        self._street = street
        self._visits = sorted(visits, key=lambda v: (v.default_start or "", (v.name or "").lower()))
        self._font_size = font_size
        self._visit_items: list[VisitItem] = []
        self._build_children()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def street(self) -> str:
        return self._street

    @property
    def visits(self) -> list[Visit]:
        return list(self._visits)

    def column_width(self) -> int:
        from controllers.route_layout_engine import _scaled
        return _scaled(VISIT_WIDTH, self._font_size)

    def header_height(self) -> int:
        from controllers.route_layout_engine import _scaled
        return _scaled(HEADER_HEIGHT // 2, self._font_size)

    def total_height(self) -> int:
        from controllers.route_layout_engine import _scaled
        vh = _scaled(VISIT_HEIGHT, self._font_size)
        return self.header_height() + len(self._visits) * vh + _PAD * 2

    def add_visit(self, visit: Visit, animate: bool = False):
        """Re-insert a visit that was dragged back from a route."""
        self._visits.append(visit)
        self._visits.sort(key=lambda v: (v.default_start or "", (v.name or "").lower()))
        self._rebuild(animate)

    def remove_visit(self, visit_id: int):
        self._visits = [v for v in self._visits if v.id != visit_id]
        self._rebuild()

    def set_font_size(self, size: int):
        self._font_size = size
        for vi in self._visit_items:
            vi.set_font_size(size)
        self.prepareGeometryChange()
        self._layout_children()
        self.update()

    def apply_filter(self, active_tags: set[str], mode: str = "or"):
        for vi in self._visit_items:
            if not active_tags:
                vi.set_greyed_out(False)
                continue
            entry_tags = {t.strip() for t in vi.entry.display_insatser.split(",") if t.strip()}
            if mode == "and":
                match = active_tags.issubset(entry_tags)
            else:
                match = bool(entry_tags & active_tags)
            vi.set_greyed_out(not match)

    def highlight_pair(self, visit_id: Optional[int]):
        for vi in self._visit_items:
            vi.set_pair_highlight(vi.entry.visit_id == visit_id)

    def set_selected_visit(self, visit_id: Optional[int]):
        for vi in self._visit_items:
            vi.set_selected(visit_id is not None and vi.entry.visit_id == visit_id)

    def find_visit_item(self, visit_id: int) -> Optional[VisitItem]:
        for vi in self._visit_items:
            if vi.entry.visit_id == visit_id:
                return vi
        return None

    # ------------------------------------------------------------------
    # QGraphicsItem
    # ------------------------------------------------------------------

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self.column_width(), self.total_height())

    def paint(self, painter: QPainter, option, widget=None):
        w = self.column_width()
        h = self.total_height()
        hh = self.header_height()
        fs = self._font_size

        painter.fillRect(0, hh, w, h - hh, QColor(COLOR_POOL_COLUMN_BG))
        painter.fillRect(0, 0, w, hh, QColor(COLOR_HEADER_BG))
        painter.setPen(QPen(QColor("#B0BEC5"), 1))
        painter.drawRect(0, 0, w - 1, h - 1)

        # Street name
        painter.setFont(QFont("Segoe UI", fs, QFont.Weight.Bold))
        painter.setPen(QColor("#212121"))
        name_rect = QRectF(_PAD, 0, w - _BTN_W * 2 - _PAD * 2, hh)
        painter.drawText(name_rect,
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         self._street)

        # Move buttons
        btn_font = QFont("Segoe UI", max(fs - 3, 7))
        painter.setFont(btn_font)
        r_left = QRectF(w - _BTN_W * 2 - 4, (hh - _BTN_H) / 2, _BTN_W, _BTN_H)
        r_right = QRectF(w - _BTN_W - 2, (hh - _BTN_H) / 2, _BTN_W, _BTN_H)
        for rect, label in [(r_left, "◀"), (r_right, "▶")]:
            painter.setPen(QPen(QColor("#90A4AE"), 1))
            painter.drawRoundedRect(rect, 3, 3)
            painter.setPen(QColor("#546E7A"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent):
        event.accept()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mouseReleaseEvent(event)
        pos = event.pos()
        w = self.column_width()
        hh = self.header_height()
        if pos.y() > hh:
            return super().mouseReleaseEvent(event)
        r_left = QRectF(w - _BTN_W * 2 - 4, (hh - _BTN_H) / 2, _BTN_W, _BTN_H)
        r_right = QRectF(w - _BTN_W - 2, (hh - _BTN_H) / 2, _BTN_W, _BTN_H)
        if r_left.contains(pos):
            self.move_left_requested.emit(self)
        elif r_right.contains(pos):
            self.move_right_requested.emit(self)
        else:
            super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _rebuild(self, animate: bool = False):
        for vi in self._visit_items:
            vi.setParentItem(None)
            if vi.scene():
                vi.scene().removeItem(vi)
        self._visit_items.clear()
        self._build_children()

    def _build_children(self):
        for visit in self._visits:
            entry = _entry_from_visit(visit, self._font_size)
            vi = VisitItem(entry, self._font_size, in_route=False, parent=self)
            vi.selected.connect(lambda v, s=self: s.visit_selected.emit(s, v))
            self._visit_items.append(vi)
        self._layout_children()

    def _layout_children(self):
        hh = self.header_height()
        y = hh + _PAD
        for vi in self._visit_items:
            vi.setPos(QPointF(0, y))
            y += vi.height()
        self.prepareGeometryChange()
