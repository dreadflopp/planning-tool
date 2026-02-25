"""RouteScene – QGraphicsScene for the left (routes) panel."""

from __future__ import annotations

import json
from typing import Optional, TYPE_CHECKING

import shiboken6
from PySide6.QtCore import Qt, QPointF, Signal
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import QGraphicsScene, QGraphicsSceneMouseEvent, QGraphicsLineItem, QInputDialog

from domain.models import Route, RouteEntry, TravelMode, Visit
from domain.constants import (
    VISIT_WIDTH, COLUMN_SPACING, MIME_POOL_VISIT, MIME_ROUTE_ENTRY, MIME_OFFICE_TEMPLATE,
    MIME_EXTRA_TIME_TEMPLATE,
)
from ui.items.route_column_item import RouteColumnItem

if TYPE_CHECKING:
    from controllers.route_layout_engine import RouteLayoutEngine
    from controllers.route_recalculation_engine import RouteRecalculationEngine


class RouteScene(QGraphicsScene):
    """
    Manages all RouteColumnItems.
    Handles drops from the pool and within routes.
    """

    column_reorder_requested = Signal(int, int)   # (route_id, direction: -1/+1)
    route_renamed = Signal(int, str)               # (route_id, new_name)
    route_notes_changed = Signal(int, str)
    route_color_changed = Signal(int, object)      # (route_id, color|None)
    route_delete_requested = Signal(int)
    entry_dropped = Signal(int, object)            # (route_id, RouteEntry-like dict)
    entry_moved = Signal(int, int, int)            # (route_id, entry_id, direction)
    entry_time_edit = Signal(int, int)             # (route_id, entry_id)
    entry_duration_changed = Signal(int, int, int) # (route_id, entry_id, delta_minutes)
    entry_color_changed = Signal(int, int, object) # (route_id, entry_id, color|None)
    entry_remove_requested = Signal(int, int)      # (route_id, entry_id)
    entry_pair_requested = Signal(int, int)        # (route_id, entry_id)
    entry_unpair_requested = Signal(int, int)      # (route_id, entry_id)
    empty_remove = Signal(int, int, int)           # (route_id, from_entry_id, to_entry_id)
    extra_time_remove = Signal(int, int)           # (route_id, to_entry_id)
    travel_mode_changed = Signal(int, int, str)    # (route_id, seg_id, mode)
    travel_duration_changed = Signal(int, int, int) # (route_id, seg_id, delta)
    travel_minutes_edit = Signal(int, int)         # (route_id, seg_id)
    travel_retry = Signal(int, int)                # (route_id, seg_id)
    travel_source_toggle = Signal(int, int)        # (route_id, seg_id)
    visit_selected = Signal(int, int)              # (route_id, entry_id)

    def __init__(self, layout_engine, parent=None):
        super().__init__(parent)
        self._layout = layout_engine
        self._column_items: list[RouteColumnItem] = []
        self._drop_indicator: Optional[QGraphicsLineItem] = None
        self._extra_time_minutes = 0
        self._show_extra_time = True
        self._inconsistent_entry_ids_by_route: dict[int, set[int]] = {}
        self._inconsistent_travel_pairs_by_route: dict[int, set[tuple[int, int]]] = {}
        self._inconsistent_empty_pairs_by_route: dict[int, set[tuple[int, int]]] = {}
        self.setBackgroundBrush(QColor("#E0E0E0"))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_routes(self, routes: list[Route]):
        self.clear()
        self._drop_indicator = None
        self._column_items.clear()
        for route in routes:
            self._add_column(route)
        self._normalize_route_orders()
        self._normalize_paired_visit_heights()
        self._reposition_columns()

    def add_route(self, route: Route):
        self._add_column(route)
        self._normalize_route_orders()
        self._normalize_paired_visit_heights()
        self._reposition_columns()

    def remove_route(self, route_id: int):
        item = self._find_column(route_id)
        if item:
            self.removeItem(item)
            self._column_items.remove(item)
        self._normalize_route_orders()
        self._normalize_paired_visit_heights()
        self._reposition_columns()

    def rebuild_route(self, route: Route, animate: bool = False):
        item = self._find_column(route.id)
        if item:
            item._route = route
            item.rebuild(animate)
            item.set_inconsistent_entries(self._inconsistent_entry_ids_by_route.get(route.id, set()))
            item.set_inconsistent_blocks(
                self._inconsistent_travel_pairs_by_route.get(route.id, set()),
                self._inconsistent_empty_pairs_by_route.get(route.id, set()),
            )
            self._normalize_paired_visit_heights()
            self._reposition_columns(animate)

    def set_inconsistent_entries(self, by_route: dict[int, set[int]]):
        self._inconsistent_entry_ids_by_route = {
            int(route_id): set(entry_ids)
            for route_id, entry_ids in (by_route or {}).items()
        }
        for col in self._column_items:
            col.set_inconsistent_entries(self._inconsistent_entry_ids_by_route.get(col.route.id, set()))

    def set_inconsistent_blocks(self,
                                travel_by_route: dict[int, set[tuple[int, int]]],
                                empty_by_route: dict[int, set[tuple[int, int]]]):
        self._inconsistent_travel_pairs_by_route = {
            int(route_id): set(pairs)
            for route_id, pairs in (travel_by_route or {}).items()
        }
        self._inconsistent_empty_pairs_by_route = {
            int(route_id): set(pairs)
            for route_id, pairs in (empty_by_route or {}).items()
        }
        for col in self._column_items:
            col.set_inconsistent_blocks(
                self._inconsistent_travel_pairs_by_route.get(col.route.id, set()),
                self._inconsistent_empty_pairs_by_route.get(col.route.id, set()),
            )

    def set_font_size(self, size: int):
        for col in self._column_items:
            col.set_font_size(size)
        self._normalize_paired_visit_heights()
        self._reposition_columns()

    def set_block_visibility(self, show_travel: bool, show_space: bool, show_extra_time: bool):
        self._show_extra_time = bool(show_extra_time)
        for col in self._column_items:
            col.set_block_visibility(show_travel, show_space, self._show_extra_time)
        self._normalize_paired_visit_heights()
        self._reposition_columns()

    def set_extra_time_minutes(self, minutes: int):
        self._extra_time_minutes = max(0, int(minutes))
        for col in self._column_items:
            col.set_extra_time_minutes(self._extra_time_minutes)
        self._normalize_paired_visit_heights()
        self._reposition_columns()

    def apply_filter(self, active_tags: set[str], mode: str = "or"):
        for col in self._column_items:
            col.apply_filter(active_tags, mode)

    def apply_search(self, query: str):
        for col in self._column_items:
            col.apply_search(query)

    def highlight_pair(self, route_id: Optional[int], entry_id: Optional[int]):
        for col in self._column_items:
            if route_id is None or col.route.id == route_id:
                col.highlight_pair(entry_id)

    def highlight_same_name_address(self, visit_id: Optional[int], all_visits: dict[int, 'Visit']):
        """Highlight visits that share name+address with the selected visit."""
        same_ids: set[int] = set()
        if visit_id:
            source_visit = all_visits.get(visit_id)
            if source_visit:
                source_name = (source_visit.name or "").strip().lower()
                source_address = (source_visit.address or "").strip().lower()
                for v in all_visits.values():
                    if v.id == visit_id:
                        continue
                    if ((v.name or "").strip().lower() == source_name and
                            (v.address or "").strip().lower() == source_address):
                        same_ids.add(v.id)

        for col in self._column_items:
            col.highlight_same_name_address(same_ids)

    def set_selected_entry(self, route_id: Optional[int], entry_id: Optional[int]):
        """Highlight the selected visit item and clear all others."""
        for col in self._column_items:
            if route_id is not None and col.route.id == route_id:
                col.set_selected_entry(entry_id)
            else:
                col.set_selected_entry(None)

    def move_column(self, route_id: int, direction: int):
        """direction: -1 = left, +1 = right."""
        items = self._ordered_columns()
        idx = next((i for i, c in enumerate(items) if c.route.id == route_id), None)
        if idx is None:
            return
        if direction <= -999:
            target = 0
        elif direction >= 999:
            target = len(items) - 1
        else:
            target = idx + direction
        if target < 0 or target >= len(items):
            return
        if target == idx:
            return

        moved = items.pop(idx)
        items.insert(target, moved)
        for order, col in enumerate(items):
            col.route.display_order = order

        self._reposition_columns(animate=True)
        self.column_reorder_requested.emit(route_id, direction)

    def pop_visit(self, route_id: int, entry_id: Optional[int] = None, visit_index: Optional[int] = None):
        col = self._find_column(route_id)
        if not col:
            return
        col.pop_visit(entry_id=entry_id, visit_index=visit_index)

    # ------------------------------------------------------------------
    # Drop handling
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event):
        if (event.mimeData().hasFormat(MIME_POOL_VISIT) or
                event.mimeData().hasFormat(MIME_ROUTE_ENTRY) or
                event.mimeData().hasFormat(MIME_OFFICE_TEMPLATE) or
                event.mimeData().hasFormat(MIME_EXTRA_TIME_TEMPLATE)):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        event.acceptProposedAction()
        mime = event.mimeData()
        if (mime.hasFormat(MIME_POOL_VISIT) or
            mime.hasFormat(MIME_OFFICE_TEMPLATE) or
            mime.hasFormat(MIME_ROUTE_ENTRY) or
            mime.hasFormat(MIME_EXTRA_TIME_TEMPLATE)):
            pos = event.scenePos()
            col = self._column_at(pos)
            if col:
                ind_y = col.drop_indicator_y(pos.y())
                self._show_drop_indicator(col.scenePos().x(), ind_y, col.column_width())
                return
        self._hide_drop_indicator()

    def dragLeaveEvent(self, event):
        self._hide_drop_indicator()

    def dropEvent(self, event):
        self._hide_drop_indicator()
        pos = event.scenePos()
        col = self._column_at(pos)
        if col is None:
            event.ignore()
            return

        mime = event.mimeData()

        if mime.hasFormat(MIME_POOL_VISIT):
            data = json.loads(bytes(mime.data(MIME_POOL_VISIT)).decode())
            insert_index = col.insert_index_for_scene_y(pos.y())
            self.entry_dropped.emit(col.route.id, {
                "type": "pool_visit",
                "visit_id": data["visit_id"],
                "insert_index": insert_index,
            })
            event.acceptProposedAction()

        elif mime.hasFormat(MIME_OFFICE_TEMPLATE):
            data = json.loads(bytes(mime.data(MIME_OFFICE_TEMPLATE)).decode())
            insert_index = col.insert_index_for_scene_y(pos.y())
            self.entry_dropped.emit(col.route.id, {
                "type": "office",
                "name": data["name"],
                "address": data["address"],
                "duration_minutes": data.get("duration_minutes", 10),
                "insert_index": insert_index,
            })
            event.acceptProposedAction()

        elif mime.hasFormat(MIME_EXTRA_TIME_TEMPLATE):
            data = json.loads(bytes(mime.data(MIME_EXTRA_TIME_TEMPLATE)).decode())
            insert_index = col.insert_index_for_scene_y(pos.y())
            self.entry_dropped.emit(col.route.id, {
                "type": "extra_time",
                "duration_minutes": data.get("duration_minutes", 0),
                "insert_index": insert_index,
            })
            event.acceptProposedAction()

        elif mime.hasFormat(MIME_ROUTE_ENTRY):
            data = json.loads(bytes(mime.data(MIME_ROUTE_ENTRY)).decode())
            insert_index = col.insert_index_for_scene_y(pos.y())
            self.entry_dropped.emit(col.route.id, {
                "type": "route_entry",
                "entry_id": data["entry_id"],
                "source_route_id": data["route_id"],
                "insert_index": insert_index,
            })
            event.acceptProposedAction()

        else:
            event.ignore()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _add_column(self, route: Route):
        col = RouteColumnItem(route, self._layout,
                              self._layout.font_size)
        col.set_block_visibility(True, True, self._show_extra_time)
        col.set_extra_time_minutes(self._extra_time_minutes)
        col.set_inconsistent_entries(self._inconsistent_entry_ids_by_route.get(route.id, set()))
        col.set_inconsistent_blocks(
            self._inconsistent_travel_pairs_by_route.get(route.id, set()),
            self._inconsistent_empty_pairs_by_route.get(route.id, set()),
        )
        self._wire_column(col)
        self.addItem(col)
        self._column_items.append(col)

    def _wire_column(self, col: RouteColumnItem):
        col.move_left_requested.connect(
            lambda c: self.move_column(c.route.id, -1))
        col.move_right_requested.connect(
            lambda c: self.move_column(c.route.id, +1))
        col.move_start_requested.connect(
            lambda c: self.move_column(c.route.id, -999))
        col.move_end_requested.connect(
            lambda c: self.move_column(c.route.id, +999))
        col.visit_selected.connect(
            lambda c, v: self.visit_selected.emit(c.route.id, v.entry.id))
        col.rename_requested.connect(self._on_rename)
        col.notes_requested.connect(self._on_notes)
        col.route_color_requested.connect(
            lambda c, color: self.route_color_changed.emit(c.route.id, color))
        col.delete_requested.connect(
            lambda c: self.route_delete_requested.emit(c.route.id))
        col.entry_move_up.connect(
            lambda c, v: self.entry_moved.emit(c.route.id, v.entry.id, -1))
        col.entry_move_down.connect(
            lambda c, v: self.entry_moved.emit(c.route.id, v.entry.id, +1))
        col.entry_time_edit.connect(
            lambda c, v: self.entry_time_edit.emit(c.route.id, v.entry.id))
        col.entry_duration_up.connect(
            lambda c, v: self.entry_duration_changed.emit(c.route.id, v.entry.id, +1))
        col.entry_duration_dn.connect(
            lambda c, v: self.entry_duration_changed.emit(c.route.id, v.entry.id, -1))
        col.entry_color_change.connect(
            lambda c, v, color: self.entry_color_changed.emit(c.route.id, v.entry.id, color))
        col.entry_remove.connect(
            lambda c, v: self.entry_remove_requested.emit(c.route.id, v.entry.id))
        col.entry_pair_requested.connect(
            lambda c, v: self.entry_pair_requested.emit(c.route.id, v.entry.id))
        col.entry_unpair_requested.connect(
            lambda c, v: self.entry_unpair_requested.emit(c.route.id, v.entry.id))
        col.empty_remove.connect(
            lambda c, e: self.empty_remove.emit(
                c.route.id,
                e.space.from_entry_id,
                e.space.to_entry_id,
            ))
        col.extra_time_remove.connect(
            lambda c, e: self.extra_time_remove.emit(
                c.route.id,
                e.block.to_entry_id,
            ))
        col.travel_mode_changed.connect(
            lambda c, t: self.travel_mode_changed.emit(c.route.id, t.segment.id, t.segment.mode))
        col.travel_duration_up.connect(
            lambda c, t: self.travel_duration_changed.emit(c.route.id, t.segment.id, +1))
        col.travel_duration_dn.connect(
            lambda c, t: self.travel_duration_changed.emit(c.route.id, t.segment.id, -1))
        col.travel_edit_minutes.connect(
            lambda c, t: self.travel_minutes_edit.emit(c.route.id, t.segment.id))
        col.travel_retry.connect(
            lambda c, t: self.travel_retry.emit(c.route.id, t.segment.id))
        col.travel_source_toggle.connect(
            lambda c, t: self.travel_source_toggle.emit(c.route.id, t.segment.id))

    def _on_rename(self, col: RouteColumnItem):
        text, ok = QInputDialog.getText(
            None, "Byt namn", "Nytt namn för rutten:",
            text=col.route.name,
        )
        if ok and text.strip():
            col.route.name = text.strip()
            self.route_renamed.emit(col.route.id, text.strip())
            col.refresh_header()
            self._reposition_columns()

    def _on_notes(self, col: RouteColumnItem):
        text, ok = QInputDialog.getMultiLineText(
            None, "Anteckningar", f"Anteckningar för {col.route.name}:",
            text=col.route.notes,
        )
        if ok:
            col.route.notes = text
            self.route_notes_changed.emit(col.route.id, text)
            col.refresh_header()
            self._reposition_columns()

    def _normalize_paired_visit_heights(self):
        visit_items_by_visit_id: dict[int, object] = {}
        for col in self._column_items:
            for visit_item in col.visit_items():
                visit_id = visit_item.entry.visit_id
                if visit_id is not None:
                    visit_items_by_visit_id[visit_id] = visit_item

        handled_visit_ids: set[int] = set()
        for visit_id, visit_item in visit_items_by_visit_id.items():
            if visit_id in handled_visit_ids:
                continue

            visit = visit_item.entry.visit
            partner_id = visit.pair_partner_id if visit else None
            if not partner_id:
                visit_item.set_height_override(None)
                handled_visit_ids.add(visit_id)
                continue

            partner_item = visit_items_by_visit_id.get(partner_id)
            if not partner_item:
                visit_item.set_height_override(None)
                handled_visit_ids.add(visit_id)
                continue

            equal_height = max(visit_item.natural_height(), partner_item.natural_height())
            visit_item.set_height_override(equal_height)
            partner_item.set_height_override(equal_height)
            handled_visit_ids.add(visit_id)
            handled_visit_ids.add(partner_id)

        for col in self._column_items:
            col.relayout_items(animate=False)

    def _reposition_columns(self, animate: bool = False):
        self._normalize_route_orders()
        items = self._ordered_columns()
        x = 0
        for col in items:
            target = QPointF(x, 0)
            if animate:
                self._layout._move_item(col, target, animate=True)
            else:
                col.setPos(target)
            x += col.column_width() + COLUMN_SPACING
        self.setSceneRect(self.itemsBoundingRect().adjusted(-10, -10, 10, 10))

    def _column_at(self, pos: QPointF) -> Optional[RouteColumnItem]:
        for col in self._column_items:
            if col.sceneBoundingRect().contains(pos):
                return col
        # Snap to nearest column if not directly over one
        if self._column_items:
            return min(self._column_items,
                       key=lambda c: abs(c.scenePos().x() - pos.x()))
        return None

    def _find_column(self, route_id: int) -> Optional[RouteColumnItem]:
        return next((c for c in self._column_items if c.route.id == route_id), None)

    def _ordered_columns(self) -> list[RouteColumnItem]:
        return sorted(
            self._column_items,
            key=lambda c: (int(c.route.display_order), int(c.route.id)),
        )

    def _normalize_route_orders(self):
        for order, col in enumerate(self._ordered_columns()):
            col.route.display_order = order

    def _show_drop_indicator(self, x: float, y: float, w: float):
        if self._drop_indicator is not None and not shiboken6.isValid(self._drop_indicator):
            self._drop_indicator = None
        if self._drop_indicator is None:
            self._drop_indicator = QGraphicsLineItem()
            pen = QPen(QColor("#1565C0"), 3)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            self._drop_indicator.setPen(pen)
            self._drop_indicator.setZValue(200)
            self.addItem(self._drop_indicator)
        try:
            self._drop_indicator.setLine(x, y, x + w, y)
            self._drop_indicator.setVisible(True)
        except RuntimeError:
            self._drop_indicator = None

    def _hide_drop_indicator(self):
        if self._drop_indicator is not None and not shiboken6.isValid(self._drop_indicator):
            self._drop_indicator = None
            return
        if self._drop_indicator:
            try:
                self._drop_indicator.setVisible(False)
            except RuntimeError:
                self._drop_indicator = None
