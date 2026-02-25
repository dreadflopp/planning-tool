"""PoolScene – QGraphicsScene for the right (visit pool) panel."""

from __future__ import annotations

import json
from typing import Optional

from PySide6.QtCore import QPointF, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsScene

from domain.models import Visit
from domain.constants import (
    VISIT_WIDTH, COLUMN_SPACING, OFFICE_TEMPLATE_HEIGHT, MIME_ROUTE_ENTRY,
)
from ui.items.pool_column_item import PoolColumnItem
from ui.items.office_template_item import OfficeTemplateItem
from ui.items.extra_time_template_item import ExtraTimeTemplateItem

_TEMPLATE_MARGIN = 8


class PoolScene(QGraphicsScene):
    """
    Manages the office template item and all street pool columns.
    Handles drops (route entries returned to pool).
    """

    entry_returned_to_pool = Signal(int)       # entry_id
    visit_selected = Signal(int)               # visit_id
    visit_pair_requested = Signal(int)         # visit_id
    visit_unpair_requested = Signal(int)       # visit_id
    column_order_changed = Signal()
    template_edit_requested = Signal(int)

    def __init__(self, layout_engine, parent=None):
        super().__init__(parent)
        self._layout = layout_engine
        self._template_items: list[OfficeTemplateItem] = []
        self._extra_time_template: Optional[ExtraTimeTemplateItem] = None
        self._column_items: list[PoolColumnItem] = []
        self._saved_order_by_street: dict[str, int] = {}
        self.setBackgroundBrush(QColor("#ECEFF1"))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, templates: list[dict], visits: list[Visit]):
        self.clear()
        self._column_items.clear()
        self._template_items.clear()
        self._extra_time_template = None

        # Default template visits at top
        for template_index, template in enumerate(templates):
            if template.get("type") == "extra_time":
                item = ExtraTimeTemplateItem(
                    duration_minutes=int(template.get("duration_minutes", 0)),
                    font_size=self._layout.font_size,
                )
                item.setVisible(int(template.get("duration_minutes", 0)) > 0)
                self.addItem(item)
                self._extra_time_template = item
                continue
            item = OfficeTemplateItem(
                name=template.get("name", "Kontor"),
                full_address=template.get("address", ""),
                duration_minutes=int(template.get("duration_minutes", 10)),
                template_index=template_index,
                font_size=self._layout.font_size,
            )
            item.edit_requested.connect(self.template_edit_requested.emit)
            self.addItem(item)
            self._template_items.append(item)

        # Group visits by street
        streets: dict[str, list[Visit]] = {}
        for v in visits:
            streets.setdefault(v.street or "Övriga", []).append(v)

        for street, street_visits in sorted(streets.items()):
            col = PoolColumnItem(street, street_visits, self._layout.font_size)
            self._wire_column(col)
            self.addItem(col)
            self._column_items.append(col)

        self._normalize_column_orders()
        self._normalize_paired_visit_heights()

        self._reposition()

    def add_visit(self, visit: Visit, animate: bool = True):
        """Return a visit to the pool (dragged back from a route)."""
        col = self._find_column_for_street(visit.street or "Övriga")
        if col:
            col.add_visit(visit, animate)
            col.setVisible(True)
        else:
            col = PoolColumnItem(visit.street or "Övriga", [visit], self._layout.font_size)
            col._display_order = self._saved_order_by_street.get(col.street, self._next_display_order())
            self._wire_column(col)
            self.addItem(col)
            self._column_items.append(col)
            self._normalize_column_orders()
        self._saved_order_by_street[col.street] = getattr(col, "_display_order", 0)
        self._normalize_paired_visit_heights()
        self._reposition(animate)

    def remove_visit(self, visit_id: int):
        """Remove a visit from the pool (it has been placed in a route)."""
        for col in self._column_items:
            for v in col.visits:
                if v.id == visit_id:
                    col.remove_visit(visit_id)
                    if not col.visits:
                        col.setVisible(False)
                    self._normalize_paired_visit_heights()
                    self._reposition()
                    return

    def set_font_size(self, size: int):
        for item in self._template_items:
            item.set_font_size(size)
        if self._extra_time_template is not None:
            self._extra_time_template.set_font_size(size)
        for col in self._column_items:
            col.set_font_size(size)
        self._normalize_paired_visit_heights()
        self._reposition()

    def set_extra_time_minutes(self, minutes: int):
        if self._extra_time_template is not None:
            self._extra_time_template.set_duration_minutes(minutes)
            self._reposition()

    def apply_filter(self, active_tags: set[str], mode: str = "or"):
        for col in self._column_items:
            col.apply_filter(active_tags, mode)

    def apply_search(self, query: str):
        for col in self._column_items:
            col.apply_search(query)

    def set_column_order_map(self, order_by_street: dict[str, int]):
        self._saved_order_by_street = {
            str(street): int(order)
            for street, order in (order_by_street or {}).items()
        }
        for col in self._column_items:
            if col.street in self._saved_order_by_street:
                col._display_order = self._saved_order_by_street[col.street]
        self._normalize_column_orders()
        self._normalize_paired_visit_heights()
        self._reposition()

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
            col.relayout_items()

    def column_order_pairs(self) -> list[tuple[str, int]]:
        return [(col.street, int(getattr(col, "_display_order", idx))) for idx, col in enumerate(self._ordered_columns())]

    def highlight_pair(self, visit_id: Optional[int]):
        for col in self._column_items:
            col.highlight_pair(visit_id)

    def highlight_same_name_address(self, visit_id: Optional[int], all_visits: dict[int, 'Visit']):
        """Highlight all visits with the same name and address (but different ID)."""
        same_ids: set[int] = set()
        if visit_id:
            source_visit = all_visits.get(visit_id)
            if source_visit:
                for v in all_visits.values():
                    if (v.id != visit_id and 
                        v.name.strip().lower() == source_visit.name.strip().lower() and
                        v.address.strip().lower() == source_visit.address.strip().lower()):
                        same_ids.add(v.id)
        for col in self._column_items:
            col.highlight_same_name_address(same_ids)

    def set_selected_visit(self, visit_id: Optional[int]):
        for col in self._column_items:
            col.set_selected_visit(visit_id)

    def move_column(self, street: str, direction: int):
        items = self._ordered_columns()
        idx = next((i for i, c in enumerate(items) if c.street == street), None)
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
            col._display_order = order
            self._saved_order_by_street[col.street] = order

        self._reposition(animate=True)
        self.column_order_changed.emit()

    # ------------------------------------------------------------------
    # Drop (route entries returning to pool)
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(MIME_ROUTE_ENTRY):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        event.acceptProposedAction()

    def dropEvent(self, event):
        if event.mimeData().hasFormat(MIME_ROUTE_ENTRY):
            data = json.loads(bytes(event.mimeData().data(MIME_ROUTE_ENTRY)).decode())
            self.entry_returned_to_pool.emit(data["entry_id"])
            event.acceptProposedAction()
        else:
            event.ignore()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _wire_column(self, col: PoolColumnItem):
        col.move_left_requested.connect(
            lambda c: self.move_column(c.street, -1))
        col.move_right_requested.connect(
            lambda c: self.move_column(c.street, +1))
        col.move_start_requested.connect(
            lambda c: self.move_column(c.street, -999))
        col.move_end_requested.connect(
            lambda c: self.move_column(c.street, +999))
        col.visit_selected.connect(
            lambda c, v: self.visit_selected.emit(v.entry.visit_id or -1))
        col.visit_pair_requested.connect(
            lambda c, v: self.visit_pair_requested.emit(v.entry.visit_id or -1))
        col.visit_unpair_requested.connect(
            lambda c, v: self.visit_unpair_requested.emit(v.entry.visit_id or -1))

    def _reposition(self, animate: bool = False):
        template_h = 0
        x_t = 0
        row_h = 0
        for item in self._template_items:
            item.setPos(x_t, 0)
            x_t += item.width() + COLUMN_SPACING
            row_h = max(row_h, item.height())
        if self._extra_time_template is not None:
            self._extra_time_template.setPos(x_t, 0)
            if self._extra_time_template.isVisible():
                x_t += self._extra_time_template.width() + COLUMN_SPACING
                row_h = max(row_h, self._extra_time_template.height())
        has_visible_template = bool(self._template_items) or (
            self._extra_time_template is not None and self._extra_time_template.isVisible()
        )
        template_h = row_h + (_TEMPLATE_MARGIN if has_visible_template else 0)

        items = self._ordered_columns()
        x = 0
        for col in items:
            col.setVisible(bool(col.visits))
            if not col.isVisible():
                continue
            target = QPointF(x, template_h)
            if animate:
                self._layout._move_item(col, target, animate=True)
            else:
                col.setPos(target)
            x += col.column_width() + COLUMN_SPACING

        self.setSceneRect(self.itemsBoundingRect().adjusted(-10, -10, 10, 10))

    def _find_column_for_street(self, street: str) -> Optional[PoolColumnItem]:
        return next((c for c in self._column_items if c.street == street), None)

    def _ordered_columns(self) -> list[PoolColumnItem]:
        index_by_id = {id(col): idx for idx, col in enumerate(self._column_items)}
        return sorted(
            self._column_items,
            key=lambda col: (
                getattr(col, "_display_order", index_by_id.get(id(col), 0)),
                index_by_id.get(id(col), 0),
            ),
        )

    def _normalize_column_orders(self):
        for order, col in enumerate(self._ordered_columns()):
            col._display_order = order
            self._saved_order_by_street[col.street] = order

    def _next_display_order(self) -> int:
        if not self._column_items:
            return 0
        return max(int(getattr(col, "_display_order", 0)) for col in self._column_items) + 1
