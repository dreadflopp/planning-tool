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
_TITLE_ROW_BASE_H = 24


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
    move_start_requested = Signal(object)
    move_end_requested = Signal(object)
    visit_selected = Signal(object, object)  # (column_item, visit_item)
    visit_pair_requested = Signal(object, object)        # (column_item, visit_item)
    visit_unpair_requested = Signal(object, object)      # (column_item, visit_item)

    def __init__(self, street: str, visits: list[Visit],
                 font_size: int = 12, parent=None):
        super().__init__(parent)
        self._street = street
        self._visits = sorted(visits, key=lambda v: (v.default_start or "", (v.name or "").lower()))
        self._font_size = font_size
        self._visit_items: list[VisitItem] = []
        self._is_collapsed = False
        self._active_tags: set[str] = set()
        self._filter_mode: str = "or"
        self._search_query: str = ""
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
        if self._is_collapsed:
            return _scaled(38, self._font_size)  # narrow for button + padding
        return _scaled(VISIT_WIDTH, self._font_size)

    def collapsed_width(self) -> int:
        from controllers.route_layout_engine import _scaled
        return _scaled(38, self._font_size)

    def is_collapsed(self) -> bool:
        return self._is_collapsed

    def toggle_collapse(self):
        self._is_collapsed = not self._is_collapsed
        self.prepareGeometryChange()
        self._layout_children()
        self.update()

    def header_height(self) -> int:
        from controllers.route_layout_engine import _scaled
        top_h = _BTN_H + _PAD * 2
        title_h = _scaled(_TITLE_ROW_BASE_H, self._font_size)
        return top_h + title_h

    def _top_row_height(self) -> int:
        return _BTN_H + _PAD * 2

    def _title_row_height(self) -> int:
        from controllers.route_layout_engine import _scaled
        return _scaled(_TITLE_ROW_BASE_H, self._font_size)

    def total_height(self) -> int:
        from controllers.route_layout_engine import _scaled
        if self._is_collapsed:
            return self.header_height()
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
        self._active_tags = set(active_tags or set())
        self._filter_mode = "and" if str(mode).lower() == "and" else "or"
        self._refresh_visit_grey_states()

    def apply_search(self, query: str):
        self._search_query = (query or "").strip().lower()
        self._refresh_visit_grey_states()

    def _refresh_visit_grey_states(self):
        for vi in self._visit_items:
            tags_ok = True
            if self._active_tags:
                entry_tags = {t.strip() for t in vi.entry.display_insatser.split(",") if t.strip()}
                if self._filter_mode == "and":
                    tags_ok = self._active_tags.issubset(entry_tags)
                else:
                    tags_ok = bool(self._active_tags & entry_tags)

            search_ok = True
            if self._search_query:
                name = (vi.entry.display_name or "").lower()
                address = (vi.entry.display_address or "").lower()
                search_ok = self._search_query in name or self._search_query in address

            vi.set_greyed_out(not (tags_ok and search_ok))

    def _column_has_search_match(self) -> bool:
        if not self._search_query:
            return False
        for vi in self._visit_items:
            name = (vi.entry.display_name or "").lower()
            address = (vi.entry.display_address or "").lower()
            if self._search_query in name or self._search_query in address:
                return True
        return False

    def highlight_pair(self, visit_id: Optional[int]):
        for vi in self._visit_items:
            vi.set_pair_highlight(vi.entry.visit_id == visit_id)

    def highlight_same_name_address(self, same_ids: set[int]):
        """Highlight visits with the same name and address."""
        for vi in self._visit_items:
            vi.set_same_name_address_highlight(vi.entry.visit_id in same_ids if vi.entry.visit_id else False)

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
        top_h = self._top_row_height()
        title_h = self._title_row_height()
        painter.fillRect(0, top_h, w, title_h, QColor("#DFE6EA"))
        painter.setPen(QPen(QColor("#CFD8DC"), 1))
        painter.drawLine(0, top_h, w, top_h)
        painter.setPen(QPen(QColor("#B0BEC5"), 1))
        painter.drawRect(0, 0, w - 1, h - 1)

        # Collapse/expand button (top-left)
        collapse_btn_x = _PAD
        collapse_btn_y = (top_h - _BTN_H) / 2
        collapse_btn_rect = QRectF(collapse_btn_x, collapse_btn_y, _BTN_W, _BTN_H)
        btn_font = QFont("Segoe UI", max(fs - 3, 7))
        painter.setFont(btn_font)
        painter.setPen(QPen(QColor("#90A4AE"), 1))
        painter.drawRoundedRect(collapse_btn_rect, 3, 3)
        painter.setPen(QColor("#546E7A"))
        collapse_char = "▼" if self._is_collapsed else "▶"
        painter.drawText(collapse_btn_rect, Qt.AlignmentFlag.AlignCenter, collapse_char)

        if self._is_collapsed:
            if self._column_has_search_match():
                painter.setPen(QPen(QColor("#FF6F00"), 2))
                painter.drawRect(1, 1, w - 2, h - 2)
            # In collapsed mode, only show the button, no text or other buttons
            return

        # Street name (visible only when expanded)
        painter.setFont(QFont("Segoe UI", fs, QFont.Weight.Bold))
        painter.setPen(QColor("#212121"))
        name_rect = QRectF(_PAD, top_h, w - _PAD * 2, title_h)
        painter.drawText(name_rect,
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         self._street)

        # Move buttons (visible only when expanded)
        btn_font = QFont("Segoe UI", max(fs - 3, 7))
        painter.setFont(btn_font)
        r_start, r_left, r_right, r_end = self._move_button_rects(w, hh)
        for rect, label in [(r_start, "≪"), (r_left, "◀"), (r_right, "▶"), (r_end, "≫")]:
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
        top_h = self._top_row_height()
        
        # Check collapse button (top-left)
        collapse_btn_rect = QRectF(_PAD, (top_h - _BTN_H) / 2, _BTN_W, _BTN_H)
        if collapse_btn_rect.contains(pos):
            self.toggle_collapse()
            event.accept()
            return
        
        if pos.y() > hh:
            return super().mouseReleaseEvent(event)
        
        if not self._is_collapsed:
            r_start, r_left, r_right, r_end = self._move_button_rects(w, hh)
            if r_start.contains(pos):
                self.move_start_requested.emit(self)
                return
            if r_left.contains(pos):
                self.move_left_requested.emit(self)
                return
            if r_right.contains(pos):
                self.move_right_requested.emit(self)
                return
            if r_end.contains(pos):
                self.move_end_requested.emit(self)
                return
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
            vi.pair_requested.connect(lambda v, s=self: s.visit_pair_requested.emit(s, v))
            vi.unpair_requested.connect(lambda v, s=self: s.visit_unpair_requested.emit(s, v))
            self._visit_items.append(vi)
        self._refresh_visit_grey_states()
        self._layout_children()

    def _layout_children(self):
        hh = self.header_height()
        y = hh + _PAD
        for vi in self._visit_items:
            if self._is_collapsed:
                vi.hide()
            else:
                vi.show()
                vi.setPos(QPointF(0, y))
                y += vi.height()
        self.prepareGeometryChange()

    def _move_button_rects(self, w: float, hh: float) -> tuple[QRectF, QRectF, QRectF, QRectF]:
        y = (self._top_row_height() - _BTN_H) / 2
        r_end = QRectF(w - _BTN_W - 2, y, _BTN_W, _BTN_H)
        r_right = QRectF(r_end.x() - _BTN_W - 2, y, _BTN_W, _BTN_H)
        r_left = QRectF(r_right.x() - _BTN_W - 2, y, _BTN_W, _BTN_H)
        r_start = QRectF(r_left.x() - _BTN_W - 2, y, _BTN_W, _BTN_H)
        return r_start, r_left, r_right, r_end
