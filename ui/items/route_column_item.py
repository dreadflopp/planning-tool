"""RouteColumnItem – container for a single route column in the scene."""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QBrush, QFontMetrics
from PySide6.QtWidgets import QGraphicsObject, QGraphicsSceneMouseEvent, QMenu

from domain.models import Route, RouteEntry, TravelSegment, EmptySpace
from domain.constants import (
    VISIT_WIDTH, HEADER_HEIGHT, COLUMN_SPACING,
    COLOR_ROUTE_COLUMN_BG, COLOR_HEADER_BG,
    COLOR_VISIT_GREEN, COLOR_VISIT_PINK, COLOR_VISIT_BLUE,
    COLOR_VISIT_RED, COLOR_VISIT_ORANGE, COLOR_VISIT_YELLOW, COLOR_VISIT_BLACK,
)
from ui.items.visit_item import VisitItem
from ui.items.travel_item import TravelItem
from ui.items.empty_space_item import EmptySpaceItem
from ui.items.extra_time_item import ExtraTimeItem

if TYPE_CHECKING:
    from controllers.route_layout_engine import RouteLayoutEngine

_BTN_W = 26
_BTN_H = 22
_PAD = 6
_TOP_ROW_H = _BTN_H + _PAD * 2
_TITLE_ROW_BASE_H = 24
_ROUTE_COLOR_HEX = {
    "green": COLOR_VISIT_GREEN,
    "pink": COLOR_VISIT_PINK,
    "blue": COLOR_VISIT_BLUE,
    "red": COLOR_VISIT_RED,
    "orange": COLOR_VISIT_ORANGE,
    "yellow": COLOR_VISIT_YELLOW,
    "black": COLOR_VISIT_BLACK,
}
_ROUTE_COLOR_OPTIONS = [
    ("Standard", None),
    ("Grön", "green"),
    ("Rosa", "pink"),
    ("Blå", "blue"),
    ("Röd", "red"),
    ("Orange", "orange"),
    ("Gul", "yellow"),
    ("Svart", "black"),
]


class RouteColumnItem(QGraphicsObject):
    """
    Renders a full route column including header (name, notes, move buttons)
    and all stacked visit / travel / empty-space child items.
    """

    move_left_requested = Signal(object)    # emits self
    move_right_requested = Signal(object)
    move_start_requested = Signal(object)
    move_end_requested = Signal(object)
    rename_requested = Signal(object)
    notes_requested = Signal(object)
    delete_requested = Signal(object)
    route_color_requested = Signal(object, object)  # (column_item, color|None)

    # Forwarded from children
    entry_move_up = Signal(object, object)       # (column_item, visit_item)
    entry_move_down = Signal(object, object)
    entry_time_edit = Signal(object, object)     # (column_item, visit_item)
    entry_duration_up = Signal(object, object)
    entry_duration_dn = Signal(object, object)
    entry_color_change = Signal(object, object, object)  # (column_item, visit_item, color)
    entry_remove = Signal(object, object)
    entry_pair_requested = Signal(object, object)        # (column_item, visit_item)
    entry_unpair_requested = Signal(object, object)      # (column_item, visit_item)
    travel_mode_changed = Signal(object, object) # (column_item, travel_item)
    travel_edit_minutes = Signal(object, object)
    travel_duration_up = Signal(object, object)
    travel_duration_dn = Signal(object, object)
    travel_retry = Signal(object, object)
    travel_source_toggle = Signal(object, object)
    empty_remove = Signal(object, object)
    extra_time_remove = Signal(object, object)
    visit_selected = Signal(object, object)      # (column_item, visit_item)

    def __init__(self, route: Route, layout_engine, font_size: int = 12, parent=None):
        super().__init__(parent)
        self._route = route
        self._layout = layout_engine
        self._font_size = font_size
        self._show_travel = True
        self._show_space = True
        self._show_extra_time = True
        self._extra_time_minutes = 0
        self._is_collapsed = False
        self._active_tags: set[str] = set()
        self._filter_mode: str = "or"
        self._search_query: str = ""
        self._inconsistent_entry_ids: set[int] = set()
        self._inconsistent_travel_pairs: set[tuple[int, int]] = set()
        self._inconsistent_empty_pairs: set[tuple[int, int]] = set()
        self._visit_items: list[VisitItem] = []
        self._travel_items: list[TravelItem] = []
        self._empty_items: list[EmptySpaceItem] = []
        self._extra_time_items: list[ExtraTimeItem] = []
        self._all_items: list = []   # ordered sequence for layout
        self.setAcceptDrops(True)
        self._build_children()
        self._layout_children()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def route(self) -> Route:
        return self._route

    def column_width(self) -> int:
        from controllers.route_layout_engine import _scaled
        if self._is_collapsed:
            return _scaled(38, self._font_size)
        return _scaled(VISIT_WIDTH, self._font_size)

    def header_height(self) -> int:
        from controllers.route_layout_engine import _scaled
        return _TOP_ROW_H + _scaled(_TITLE_ROW_BASE_H, self._font_size)

    def _title_row_height(self) -> int:
        from controllers.route_layout_engine import _scaled
        return _scaled(_TITLE_ROW_BASE_H, self._font_size)

    def refresh_header(self):
        self.prepareGeometryChange()
        self._layout_children()
        self.update()

    def _name_rect(self, w: float) -> QRectF:
        title_y = _TOP_ROW_H
        return QRectF(_PAD, title_y, w - _PAD * 2, self._title_row_height())

    def _notes_rect(self, w: float, hh: float) -> QRectF:
        y = _TOP_ROW_H + self._title_row_height()
        return QRectF(_PAD, y, w - _PAD * 2, 0)

    def _mix_with_white(self, color: QColor, ratio: float) -> QColor:
        ratio = max(0.0, min(1.0, float(ratio)))
        r = int(color.red() * (1.0 - ratio) + 255 * ratio)
        g = int(color.green() * (1.0 - ratio) + 255 * ratio)
        b = int(color.blue() * (1.0 - ratio) + 255 * ratio)
        return QColor(r, g, b)

    def _route_tint_colors(self) -> tuple[QColor, QColor]:
        key = self._route.route_color or ""
        base_hex = _ROUTE_COLOR_HEX.get(key)
        if not base_hex:
            return QColor(COLOR_ROUTE_COLUMN_BG), QColor(COLOR_HEADER_BG)
        base = QColor(base_hex)
        if not base.isValid():
            return QColor(COLOR_ROUTE_COLUMN_BG), QColor(COLOR_HEADER_BG)
        body = self._mix_with_white(base, 0.82)
        header = self._mix_with_white(base, 0.88)
        return body, header

    def _move_button_rects(self, w: float) -> tuple[QRectF, QRectF, QRectF, QRectF]:
        y = _PAD
        r_end = QRectF(w - _BTN_W - 2, y, _BTN_W, _BTN_H)
        r_right = QRectF(r_end.x() - _BTN_W - 2, y, _BTN_W, _BTN_H)
        r_left = QRectF(r_right.x() - _BTN_W - 2, y, _BTN_W, _BTN_H)
        r_start = QRectF(r_left.x() - _BTN_W - 2, y, _BTN_W, _BTN_H)
        return r_start, r_left, r_right, r_end

    def total_height(self) -> int:
        return self.header_height() + sum(i.height() for i in self._all_items) + _PAD * 2

    def rebuild(self, animate: bool = False):
        """Rebuild child items from the route's current data and re-layout."""
        old_positions: dict[tuple, list[float]] = {}
        for item in self._all_items:
            key = self._item_identity(item)
            old_positions.setdefault(key, []).append(item.pos().y())

        for item in self._visit_items + self._travel_items + self._empty_items + self._extra_time_items:
            item.setParentItem(None)
            if item.scene():
                item.scene().removeItem(item)

        self._visit_items.clear()
        self._travel_items.clear()
        self._empty_items.clear()
        self._extra_time_items.clear()
        self._all_items.clear()
        positioned_item_ids = self._build_children(old_positions=old_positions)
        if animate:
            self._prepare_new_visits_fade_in(positioned_item_ids)
            self._seed_new_item_positions(positioned_item_ids)
        self._layout_children(animate)
        self.update()

    def set_font_size(self, size: int):
        self._font_size = size
        for item in self._visit_items + self._travel_items + self._empty_items + self._extra_time_items:
            item.set_font_size(size)
        self.prepareGeometryChange()
        self._layout_children()
        self.update()

    def toggle_collapse(self):
        """Toggle collapsed state of this route column."""
        self._is_collapsed = not self._is_collapsed
        for item in self._all_items:
            if self._is_collapsed:
                item.hide()
            else:
                item.show()
        self.prepareGeometryChange()
        self._layout_children()
        self.update()

    def set_block_visibility(self, show_travel: bool, show_space: bool, show_extra_time: bool):
        if (self._show_travel == show_travel and
                self._show_space == show_space and
                self._show_extra_time == show_extra_time):
            return
        self._show_travel = show_travel
        self._show_space = show_space
        self._show_extra_time = show_extra_time
        self.rebuild(animate=False)

    def set_extra_time_minutes(self, minutes: int):
        self._extra_time_minutes = max(0, int(minutes))
        self.rebuild(animate=False)

    def apply_filter(self, active_tags: set[str], mode: str = "or"):
        """Grey out visits that do not match current filter and search query."""
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

    def highlight_pair(self, entry_id: Optional[int]):
        for vi in self._visit_items:
            vi.set_pair_highlight(vi.entry.id == entry_id)

    def highlight_same_name_address(self, same_ids: set[int]):
        """Highlight visits that match the selected visit's name and address."""
        for vi in self._visit_items:
            visit_id = vi.entry.visit_id
            vi.set_same_name_address_highlight(bool(visit_id and visit_id in same_ids))

    def set_selected_entry(self, entry_id: Optional[int]):
        for vi in self._visit_items:
            vi.set_selected(entry_id is not None and vi.entry.id == entry_id)

    def set_inconsistent_entries(self, entry_ids: set[int]):
        self._inconsistent_entry_ids = set(entry_ids or set())
        for vi in self._visit_items:
            vi.set_inconsistent(bool(vi.entry.id in self._inconsistent_entry_ids))

    def set_inconsistent_blocks(self,
                                travel_pairs: set[tuple[int, int]],
                                empty_pairs: set[tuple[int, int]]):
        self._inconsistent_travel_pairs = set(travel_pairs or set())
        self._inconsistent_empty_pairs = set(empty_pairs or set())
        for ti in self._travel_items:
            key = (ti.segment.from_entry_id, ti.segment.to_entry_id)
            ti.set_inconsistent(key in self._inconsistent_travel_pairs)
        for ei in self._empty_items:
            key = (ei.space.from_entry_id, ei.space.to_entry_id)
            ei.set_inconsistent(key in self._inconsistent_empty_pairs)

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

    def insert_index_for_scene_y(self, scene_y: float) -> int:
        """Return route-entry insertion index for a given scene Y position."""
        local_y = scene_y - self.scenePos().y()
        hh = self.header_height()
        y = hh + _PAD
        entries = self._route.sorted_entries()
        visit_idx = 0
        for item in self._all_items:
            if isinstance(item, VisitItem):
                if local_y <= y + item.height() / 2:
                    return visit_idx
                visit_idx += 1
            y += item.height()
        return len(entries)

    def visit_item_for_entry(self, entry_id: int) -> Optional[VisitItem]:
        for vi in self._visit_items:
            if vi.entry.id == entry_id:
                return vi
        return None

    def pop_visit(self, entry_id: Optional[int] = None, visit_index: Optional[int] = None):
        visit_item: Optional[VisitItem] = None
        if entry_id is not None:
            visit_item = self.visit_item_for_entry(entry_id)
        elif visit_index is not None:
            visit_items = [item for item in self._all_items if isinstance(item, VisitItem)]
            if visit_items:
                idx = max(0, min(int(visit_index), len(visit_items) - 1))
                visit_item = visit_items[idx]
        if visit_item:
            visit_items = [item for item in self._all_items if isinstance(item, VisitItem)]
            group_h = 0
            if visit_item in visit_items:
                group_h = self._visit_group_height_for_index(visit_items.index(visit_item))
            visit_item.play_drop_pop(group_h)

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

        route_body_bg, route_header_bg = self._route_tint_colors()
        # Column background (tinted by route color)
        painter.fillRect(0, hh, w, h - hh, route_body_bg)
        # Header background: tinted (including behind buttons)
        painter.fillRect(0, 0, w, hh, route_header_bg)
        title_h = self._title_row_height()
        painter.fillRect(0, _TOP_ROW_H, w, title_h, self._mix_with_white(route_header_bg, 0.05))
        painter.setPen(QPen(QColor("#CFD8DC"), 1))
        painter.drawLine(0, _TOP_ROW_H, w, _TOP_ROW_H)
        painter.setPen(QPen(QColor("#B0BEC5"), 1))
        painter.drawRect(0, 0, w - 1, h - 1)

        # Collapse/expand button (top-left)
        collapse_btn_x = _PAD
        collapse_btn_y = _PAD
        collapse_btn_rect = QRectF(collapse_btn_x, collapse_btn_y, _BTN_W, _BTN_H)
        btn_font = QFont("Segoe UI", max(fs - 3, 7))
        painter.setFont(btn_font)
        painter.setBrush(QBrush(QColor("#ECEFF1")))
        painter.setPen(QPen(QColor("#90A4AE"), 1))
        painter.drawRoundedRect(collapse_btn_rect, 3, 3)
        painter.setPen(QColor("#546E7A"))
        collapse_char = "▼" if self._is_collapsed else "▶"
        painter.drawText(collapse_btn_rect, Qt.AlignmentFlag.AlignCenter, collapse_char)

        if self._is_collapsed:
            if self._column_has_search_match():
                painter.setPen(QPen(QColor("#FF6F00"), 2))
                painter.drawRect(1, 1, w - 2, h - 2)
            # In collapsed mode, only show the button
            return

        # Route name (double-click editable, visible only when expanded)
        name_font = QFont("Segoe UI", fs + 1, QFont.Weight.Bold)
        painter.setFont(name_font)
        painter.setPen(QColor("#212121"))
        name_rect = self._name_rect(w)
        painter.drawText(name_rect,
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         self._route.name)

        # Move left / move right buttons (visible only when expanded)
        btn_y = _PAD
        btn_font = QFont("Segoe UI", max(fs - 3, 7))
        painter.setFont(btn_font)
        r_start, r_left, r_right, r_end = self._move_button_rects(w)
        for rect, label in [(r_start, "≪"), (r_left, "◀"), (r_right, "▶"), (r_end, "≫")]:
            painter.setBrush(QBrush(QColor("#ECEFF1")))
            painter.setPen(QPen(QColor("#90A4AE"), 1))
            painter.drawRoundedRect(rect, 3, 3)
            painter.setPen(QColor("#546E7A"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)

        # Header is intentionally two rows: buttons row + title row.

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent):
        event.accept()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent):
        pos = event.pos()
        w = self.column_width()
        hh = self.header_height()
        
        # Check collapse button (top-left)
        collapse_btn_rect = QRectF(_PAD, _PAD, _BTN_W, _BTN_H)
        if collapse_btn_rect.contains(pos):
            self.toggle_collapse()
            event.accept()
            return
        
        if pos.y() > hh:
            return super().mouseReleaseEvent(event)

        if event.button() == Qt.MouseButton.RightButton:
            menu = QMenu()
            color_menu = menu.addMenu("Ruttfärg")
            for label, color_key in _ROUTE_COLOR_OPTIONS:
                act = color_menu.addAction(label)
                if (self._route.route_color or None) == color_key:
                    act.setCheckable(True)
                    act.setChecked(True)
                act.triggered.connect(
                    lambda _checked=False, c=color_key: self.route_color_requested.emit(self, c)
                )
            menu.addSeparator()
            act_rename = menu.addAction("Byt namn")
            act_notes = menu.addAction("Redigera anteckningar")
            menu.addSeparator()
            act_delete = menu.addAction("Ta bort rutt")
            chosen = menu.exec(event.screenPos())
            if chosen == act_rename:
                self.rename_requested.emit(self)
                event.accept()
                return
            if chosen == act_notes:
                self.notes_requested.emit(self)
                event.accept()
                return
            if chosen == act_delete:
                self.delete_requested.emit(self)
                event.accept()
                return
            event.accept()
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return super().mouseReleaseEvent(event)

        if self._is_collapsed:
            # Skip move button processing when collapsed
            return super().mouseReleaseEvent(event)

        # Move buttons (top row)
        r_start, r_left, r_right, r_end = self._move_button_rects(w)
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

    def mouseDoubleClickEvent(self, event: QGraphicsSceneMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.pos()
            w = self.column_width()
            hh = self.header_height()
            if pos.y() <= hh:
                if self._name_rect(w).contains(pos):
                    self.rename_requested.emit(self)
                    event.accept()
                    return
                if self._notes_rect(w, hh).contains(pos):
                    self.notes_requested.emit(self)
                    event.accept()
                    return
        super().mouseDoubleClickEvent(event)

    # ------------------------------------------------------------------
    # Drop events (visits from pool or from other routes)
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event):
        if (event.mimeData().hasFormat("application/x-pool-visit") or
                event.mimeData().hasFormat("application/x-route-entry") or
                event.mimeData().hasFormat("application/x-office-template") or
                event.mimeData().hasFormat("application/x-extra-time-template")):
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

    def _item_identity(self, item) -> tuple:
        if isinstance(item, VisitItem):
            entry = item.entry
            return (
                "visit",
                entry.id,
                entry.route_id,

                entry.visit_id,
                entry.is_office_instance,
                entry.office_name,
                entry.start_time,
                entry.end_time,
            )
        if isinstance(item, TravelItem):
            seg = item.segment
            return (
                "travel",
                seg.from_entry_id,
                seg.to_entry_id,
                seg.mode,
                seg.travel_minutes,
            )
        if isinstance(item, EmptySpaceItem):
            sp = item.space
            return (
                "empty",
                sp.from_entry_id,
                sp.to_entry_id,
                sp.duration_minutes,
            )
        if isinstance(item, ExtraTimeItem):
            block = item.block
            return (
                "extra_time",
                block.to_entry_id,
            )
        return ("unknown", id(item))

    def _build_children(self, old_positions: Optional[dict[tuple, list[float]]] = None) -> set[int]:
        positioned_item_ids: set[int] = set()
        entries = self._route.sorted_entries()
        for i, entry in enumerate(entries):
            entry.route_color = self._route.route_color
            vi = VisitItem(entry, self._font_size, in_route=True, parent=self)
            vi.set_inconsistent(bool(entry.id in self._inconsistent_entry_ids))
            self._connect_visit_item(vi)
            self._visit_items.append(vi)
            self._all_items.append(vi)
            if old_positions is not None:
                key = self._item_identity(vi)
                ys = old_positions.get(key)
                if ys:
                    vi.setPos(QPointF(0, ys.pop(0)))
                    positioned_item_ids.add(id(vi))

            if i < len(entries) - 1:
                e_next = entries[i + 1]
                seg = self._route.travel_segment_between(entry.id, e_next.id)
                if self._show_travel and seg and seg.travel_minutes > 0:
                    ti = TravelItem(seg, self._font_size, parent=self)
                    ti.set_inconsistent(
                        (seg.from_entry_id, seg.to_entry_id) in self._inconsistent_travel_pairs
                    )
                    self._connect_travel_item(ti)
                    self._travel_items.append(ti)
                    self._all_items.append(ti)
                    if old_positions is not None:
                        key = self._item_identity(ti)
                        ys = old_positions.get(key)
                        if ys:
                            ti.setPos(QPointF(0, ys.pop(0)))
                            positioned_item_ids.add(id(ti))

                block = self._route.extra_time_for_entry(e_next.id)
                if block and self._extra_time_minutes > 0 and self._show_extra_time:
                    xi = ExtraTimeItem(block, self._extra_time_minutes, self._font_size, parent=self)
                    self._connect_extra_time_item(xi)
                    self._extra_time_items.append(xi)
                    self._all_items.append(xi)
                    if old_positions is not None:
                        key = self._item_identity(xi)
                        ys = old_positions.get(key)
                        if ys:
                            xi.setPos(QPointF(0, ys.pop(0)))
                            positioned_item_ids.add(id(xi))

                esp = self._route.empty_space_between(entry.id, e_next.id)
                if self._show_space and esp and esp.duration_minutes > 0:
                    ei = EmptySpaceItem(esp, self._font_size, parent=self)
                    ei.set_inconsistent(
                        (esp.from_entry_id, esp.to_entry_id) in self._inconsistent_empty_pairs
                    )
                    self._connect_empty_item(ei)
                    self._empty_items.append(ei)
                    self._all_items.append(ei)
                    if old_positions is not None:
                        key = self._item_identity(ei)
                        ys = old_positions.get(key)
                        if ys:
                            ei.setPos(QPointF(0, ys.pop(0)))
                            positioned_item_ids.add(id(ei))
        self._refresh_visit_grey_states()
        return positioned_item_ids

    def _target_positions(self) -> dict[object, float]:
        hh = self.header_height()
        y = hh + _PAD
        out: dict[object, float] = {}
        for item in self._all_items:
            out[item] = float(y)
            y += item.height()
        return out

    def _seed_new_item_positions(self, positioned_item_ids: set[int]):
        """Seed items lacking previous positions from nearby anchors to avoid top-fly-in."""
        targets = self._target_positions()
        for idx, item in enumerate(self._all_items):
            if id(item) in positioned_item_ids:
                continue
            target_y = targets[item]
            prev_anchor: Optional[float] = None
            next_anchor: Optional[float] = None

            for j in range(idx - 1, -1, -1):
                prev = self._all_items[j]
                if id(prev) in positioned_item_ids:
                    prev_anchor = prev.pos().y() + prev.height()
                    break

            for j in range(idx + 1, len(self._all_items)):
                nxt = self._all_items[j]
                if id(nxt) in positioned_item_ids:
                    next_anchor = nxt.pos().y()
                    break

            if prev_anchor is not None and next_anchor is not None:
                seed_y = (prev_anchor + next_anchor - item.height()) / 2.0
            elif prev_anchor is not None:
                seed_y = prev_anchor
            elif next_anchor is not None:
                seed_y = next_anchor - item.height()
            else:
                seed_y = target_y

            if isinstance(item, VisitItem):
                visit_items = [v for v in self._all_items if isinstance(v, VisitItem)]
                if item in visit_items:
                    visit_idx = visit_items.index(item)
                    group_h = self._visit_group_height_for_index(visit_idx)
                    max_down = max(0.0, float(group_h - item.height()))
                    seed_y = max(target_y - 20.0, min(seed_y, target_y + max_down))

            item.setPos(QPointF(0.0, float(seed_y)))

    def _prepare_new_visits_fade_in(self, positioned_item_ids: set[int]):
        """Place new visit items directly at final Y and fade in after gap motion."""
        targets = self._target_positions()
        delay = int(getattr(self._layout, "ANIMATE_DURATION_MS", 280))
        for item in self._all_items:
            if not isinstance(item, VisitItem):
                continue
            if id(item) in positioned_item_ids:
                continue
            target_y = targets[item]
            item.setPos(QPointF(0.0, float(target_y)))
            item.play_insert_fade(delay_ms=delay, duration_ms=180)
            positioned_item_ids.add(id(item))

    def _visit_group_height_for_index(self, visit_index: int) -> int:
        """Height for one visit bundle: visit + following non-visit blocks until next visit."""
        visits = [item for item in self._all_items if isinstance(item, VisitItem)]
        if not visits:
            return 0
        idx = max(0, min(int(visit_index), len(visits) - 1))
        visit_item = visits[idx]
        try:
            start = self._all_items.index(visit_item)
        except ValueError:
            return visit_item.height()

        total = 0
        for i in range(start, len(self._all_items)):
            item = self._all_items[i]
            if i > start and isinstance(item, VisitItem):
                break
            total += item.height()
        return total

    def _layout_children(self, animate: bool = False):
        hh = self.header_height()
        y = hh + _PAD
        for item in self._all_items:
            self._layout._move_item(item, QPointF(0, y), animate=animate)
            y += item.height()
        self.prepareGeometryChange()

    def _connect_visit_item(self, vi: VisitItem):
        vi.move_up_requested.connect(lambda v: self.entry_move_up.emit(self, v))
        vi.move_down_requested.connect(lambda v: self.entry_move_down.emit(self, v))
        vi.time_edit_requested.connect(lambda v: self.entry_time_edit.emit(self, v))
        vi.duration_up_requested.connect(lambda v: self.entry_duration_up.emit(self, v))
        vi.duration_down_requested.connect(lambda v: self.entry_duration_dn.emit(self, v))
        vi.color_change_requested.connect(lambda v, c: self.entry_color_change.emit(self, v, c))
        vi.remove_requested.connect(lambda v: self.entry_remove.emit(self, v))
        vi.pair_requested.connect(lambda v: self.entry_pair_requested.emit(self, v))
        vi.unpair_requested.connect(lambda v: self.entry_unpair_requested.emit(self, v))
        vi.selected.connect(lambda v, s=self: s.visit_selected.emit(s, v))

    def _connect_travel_item(self, ti: TravelItem):
        ti.mode_changed.connect(lambda t: self.travel_mode_changed.emit(self, t))
        ti.edit_minutes_requested.connect(lambda t: self.travel_edit_minutes.emit(self, t))
        ti.duration_up_requested.connect(lambda t: self.travel_duration_up.emit(self, t))
        ti.duration_down_requested.connect(lambda t: self.travel_duration_dn.emit(self, t))
        ti.retry_requested.connect(lambda t: self.travel_retry.emit(self, t))
        ti.source_toggle_requested.connect(lambda t: self.travel_source_toggle.emit(self, t))

    def _connect_empty_item(self, ei: EmptySpaceItem):
        ei.remove_requested.connect(lambda e: self.empty_remove.emit(self, e))

    def _connect_extra_time_item(self, xi: ExtraTimeItem):
        xi.remove_requested.connect(lambda e: self.extra_time_remove.emit(self, e))
