"""RouteColumnItem – container for a single route column in the scene."""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QBrush
from PySide6.QtWidgets import QGraphicsObject, QGraphicsSceneMouseEvent

from domain.models import Route, RouteEntry, TravelSegment, EmptySpace
from domain.constants import (
    VISIT_WIDTH, HEADER_HEIGHT, COLUMN_SPACING,
    COLOR_ROUTE_COLUMN_BG, COLOR_HEADER_BG,
)
from ui.items.visit_item import VisitItem
from ui.items.travel_item import TravelItem
from ui.items.empty_space_item import EmptySpaceItem

if TYPE_CHECKING:
    from controllers.route_layout_engine import RouteLayoutEngine

_BTN_W = 26
_BTN_H = 22
_PAD = 6


class RouteColumnItem(QGraphicsObject):
    """
    Renders a full route column including header (name, notes, move buttons)
    and all stacked visit / travel / empty-space child items.
    """

    move_left_requested = Signal(object)    # emits self
    move_right_requested = Signal(object)
    rename_requested = Signal(object)
    notes_requested = Signal(object)
    delete_requested = Signal(object)

    # Forwarded from children
    entry_move_up = Signal(object, object)       # (column_item, visit_item)
    entry_move_down = Signal(object, object)
    entry_time_edit = Signal(object, object)     # (column_item, visit_item)
    entry_duration_up = Signal(object, object)
    entry_duration_dn = Signal(object, object)
    travel_mode_changed = Signal(object, object) # (column_item, travel_item)
    travel_edit_minutes = Signal(object, object)
    travel_restore = Signal(object, object)
    visit_selected = Signal(object, object)      # (column_item, visit_item)

    def __init__(self, route: Route, layout_engine, font_size: int = 12, parent=None):
        super().__init__(parent)
        self._route = route
        self._layout = layout_engine
        self._font_size = font_size
        self._visit_items: list[VisitItem] = []
        self._travel_items: list[TravelItem] = []
        self._empty_items: list[EmptySpaceItem] = []
        self._all_items: list = []   # ordered sequence for layout
        self.setAcceptDrops(True)
        self._build_children()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def route(self) -> Route:
        return self._route

    def column_width(self) -> int:
        from controllers.route_layout_engine import _scaled
        return _scaled(VISIT_WIDTH, self._font_size)

    def header_height(self) -> int:
        from controllers.route_layout_engine import _scaled
        return _scaled(HEADER_HEIGHT, self._font_size)

    def total_height(self) -> int:
        return self.header_height() + sum(i.height() for i in self._all_items) + _PAD * 2

    def rebuild(self, animate: bool = False):
        """Rebuild child items from the route's current data and re-layout."""
        for item in self._visit_items + self._travel_items + self._empty_items:
            item.setParentItem(None)
            if item.scene():
                item.scene().removeItem(item)

        self._visit_items.clear()
        self._travel_items.clear()
        self._empty_items.clear()
        self._all_items.clear()
        self._build_children()
        self._layout_children(animate)
        self.update()

    def set_font_size(self, size: int):
        self._font_size = size
        for item in self._visit_items + self._travel_items + self._empty_items:
            item.set_font_size(size)
        self.prepareGeometryChange()
        self._layout_children()
        self.update()

    def apply_filter(self, active_tags: set[str]):
        """Grey out visits that do not contain any of the active_tags."""
        for vi in self._visit_items:
            if not active_tags:
                vi.set_greyed_out(False)
                continue
            entry_tags = {t.strip() for t in vi.entry.display_insatser.split(",") if t.strip()}
            vi.set_greyed_out(not bool(entry_tags & active_tags))

    def highlight_pair(self, entry_id: Optional[int]):
        for vi in self._visit_items:
            vi.set_pair_highlight(vi.entry.id == entry_id)

    def set_selected_entry(self, entry_id: Optional[int]):
        for vi in self._visit_items:
            vi.set_selected(entry_id is not None and vi.entry.id == entry_id)

    def drop_indicator_y(self, scene_y: float) -> float:
        """Return scene-Y for the drop indicator line given a scene drag position."""
        local_y = scene_y - self.scenePos().y()
        hh = self.header_height()
        y = hh + _PAD
        for item in self._all_items:
            if local_y <= y + item.height() / 2:
                return self.scenePos().y() + y
            y += item.height()
        return self.scenePos().y() + y

    def visit_item_for_entry(self, entry_id: int) -> Optional[VisitItem]:
        for vi in self._visit_items:
            if vi.entry.id == entry_id:
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

        # Column background
        painter.fillRect(0, hh, w, h - hh, QColor(COLOR_ROUTE_COLUMN_BG))
        # Header background
        painter.fillRect(0, 0, w, hh, QColor(COLOR_HEADER_BG))
        painter.setPen(QPen(QColor("#B0BEC5"), 1))
        painter.drawRect(0, 0, w - 1, h - 1)

        # Route name
        name_font = QFont("Segoe UI", fs + 1, QFont.Weight.Bold)
        painter.setFont(name_font)
        painter.setPen(QColor("#212121"))
        name_rect = QRectF(_PAD, _PAD, w - _BTN_W * 2 - _PAD * 2, hh * 0.38)
        painter.drawText(name_rect,
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         self._route.name)

        # Move left / move right buttons
        btn_y = _PAD
        btn_font = QFont("Segoe UI", max(fs - 3, 7))
        painter.setFont(btn_font)
        r_left = QRectF(w - _BTN_W * 2 - 4, btn_y, _BTN_W, _BTN_H)
        r_right = QRectF(w - _BTN_W - 2, btn_y, _BTN_W, _BTN_H)
        for rect, label in [(r_left, "◀"), (r_right, "▶")]:
            painter.setPen(QPen(QColor("#90A4AE"), 1))
            painter.drawRoundedRect(rect, 3, 3)
            painter.setPen(QColor("#546E7A"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)

        # Notes / rename buttons
        btn_y2 = hh * 0.45
        r_rename = QRectF(_PAD, btn_y2, w * 0.45 - _PAD, _BTN_H)
        r_notes = QRectF(w * 0.45, btn_y2, w * 0.45 - _PAD, _BTN_H)
        r_delete = QRectF(w - _BTN_W - 2, btn_y2, _BTN_W, _BTN_H)
        for rect, label, color in [
            (r_rename, "Byt namn", "#78909C"),
            (r_notes, "Anteckningar", "#78909C"),
            (r_delete, "✕", "#EF5350"),
        ]:
            painter.setPen(QPen(QColor(color), 1))
            painter.drawRoundedRect(rect, 3, 3)
            painter.setPen(QColor(color))
            painter.setFont(QFont("Segoe UI", max(fs - 3, 7)))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)

        # Notes preview
        if self._route.notes:
            painter.setFont(QFont("Segoe UI", max(fs - 3, 7)))
            painter.setPen(QColor("#546E7A"))
            notes_rect = QRectF(_PAD, hh * 0.72, w - _PAD * 2, hh * 0.24)
            painter.drawText(notes_rect,
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             self._route.notes[:60])

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent):
        event.accept()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mouseReleaseEvent(event)
        pos = event.pos()
        w = self.column_width()
        hh = self.header_height()
        fs = self._font_size

        if pos.y() > hh:
            return super().mouseReleaseEvent(event)

        # Move buttons (top row)
        btn_y = _PAD
        r_left = QRectF(w - _BTN_W * 2 - 4, btn_y, _BTN_W, _BTN_H)
        r_right = QRectF(w - _BTN_W - 2, btn_y, _BTN_W, _BTN_H)
        if r_left.contains(pos):
            self.move_left_requested.emit(self)
            return
        if r_right.contains(pos):
            self.move_right_requested.emit(self)
            return

        btn_y2 = hh * 0.45
        r_rename = QRectF(_PAD, btn_y2, w * 0.45 - _PAD, _BTN_H)
        r_notes = QRectF(w * 0.45, btn_y2, w * 0.45 - _PAD, _BTN_H)
        r_delete = QRectF(w - _BTN_W - 2, btn_y2, _BTN_W, _BTN_H)
        if r_rename.contains(pos):
            self.rename_requested.emit(self)
            return
        if r_notes.contains(pos):
            self.notes_requested.emit(self)
            return
        if r_delete.contains(pos):
            self.delete_requested.emit(self)
            return

        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------
    # Drop events (visits from pool or from other routes)
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event):
        if (event.mimeData().hasFormat("application/x-pool-visit") or
                event.mimeData().hasFormat("application/x-route-entry") or
                event.mimeData().hasFormat("application/x-office-template")):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        event.acceptProposedAction()

    def dropEvent(self, event):
        # Let the scene handle it (scene's dropEvent has full context)
        event.ignore()  # propagate to scene

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_children(self):
        entries = self._route.sorted_entries()
        for i, entry in enumerate(entries):
            vi = VisitItem(entry, self._font_size, in_route=True, parent=self)
            self._connect_visit_item(vi)
            self._visit_items.append(vi)
            self._all_items.append(vi)

            if i < len(entries) - 1:
                e_next = entries[i + 1]
                seg = self._route.travel_segment_between(entry.id, e_next.id)
                if seg and seg.travel_minutes > 0:
                    ti = TravelItem(seg, self._font_size, parent=self)
                    self._connect_travel_item(ti)
                    self._travel_items.append(ti)
                    self._all_items.append(ti)

                esp = self._route.empty_space_between(entry.id, e_next.id)
                if esp and esp.duration_minutes > 0:
                    ei = EmptySpaceItem(esp, self._font_size, parent=self)
                    self._empty_items.append(ei)
                    self._all_items.append(ei)

        self._layout_children()

    def _layout_children(self, animate: bool = False):
        hh = self.header_height()
        y = hh + _PAD
        for item in self._all_items:
            item.setPos(QPointF(0, y))
            y += item.height()
        self.prepareGeometryChange()

    def _connect_visit_item(self, vi: VisitItem):
        vi.move_up_requested.connect(lambda v: self.entry_move_up.emit(self, v))
        vi.move_down_requested.connect(lambda v: self.entry_move_down.emit(self, v))
        vi.time_edit_requested.connect(lambda v: self.entry_time_edit.emit(self, v))
        vi.duration_up_requested.connect(lambda v: self.entry_duration_up.emit(self, v))
        vi.duration_down_requested.connect(lambda v: self.entry_duration_dn.emit(self, v))
        vi.selected.connect(lambda v, s=self: s.visit_selected.emit(s, v))

    def _connect_travel_item(self, ti: TravelItem):
        ti.mode_changed.connect(lambda t: self.travel_mode_changed.emit(self, t))
        ti.edit_minutes_requested.connect(lambda t: self.travel_edit_minutes.emit(self, t))
        ti.restore_calculated_requested.connect(lambda t: self.travel_restore.emit(self, t))
