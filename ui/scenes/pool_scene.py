"""PoolScene – QGraphicsScene for the right (visit pool) panel."""

from __future__ import annotations

import json
from typing import Optional

from PySide6.QtCore import QPointF, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsScene

from domain.models import Visit, OfficeTemplate
from domain.constants import (
    VISIT_WIDTH, COLUMN_SPACING, OFFICE_TEMPLATE_HEIGHT, MIME_ROUTE_ENTRY,
)
from ui.items.pool_column_item import PoolColumnItem
from ui.items.office_template_item import OfficeTemplateItem

_TEMPLATE_MARGIN = 8


class PoolScene(QGraphicsScene):
    """
    Manages the office template item and all street pool columns.
    Handles drops (route entries returned to pool).
    """

    office_edit_requested = Signal()
    entry_returned_to_pool = Signal(int)       # entry_id
    visit_selected = Signal(int)               # visit_id

    def __init__(self, layout_engine, parent=None):
        super().__init__(parent)
        self._layout = layout_engine
        self._template_item: Optional[OfficeTemplateItem] = None
        self._column_items: list[PoolColumnItem] = []
        self.setBackgroundBrush(QColor("#ECEFF1"))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, template: OfficeTemplate, visits: list[Visit]):
        self.clear()
        self._column_items.clear()
        self._template_item = None

        # Office template at top
        self._template_item = OfficeTemplateItem(template, self._layout.font_size)
        self._template_item.template_changed.connect(self.office_edit_requested)
        self.addItem(self._template_item)

        # Group visits by street
        streets: dict[str, list[Visit]] = {}
        for v in visits:
            streets.setdefault(v.street or "Övriga", []).append(v)

        for street, street_visits in sorted(streets.items()):
            col = PoolColumnItem(street, street_visits, self._layout.font_size)
            self._wire_column(col)
            self.addItem(col)
            self._column_items.append(col)

        self._reposition()

    def add_visit(self, visit: Visit, animate: bool = True):
        """Return a visit to the pool (dragged back from a route)."""
        col = self._find_column_for_street(visit.street or "Övriga")
        if col:
            col.add_visit(visit, animate)
        else:
            col = PoolColumnItem(visit.street or "Övriga", [visit], self._layout.font_size)
            self._wire_column(col)
            self.addItem(col)
            self._column_items.append(col)
        self._reposition(animate)

    def remove_visit(self, visit_id: int):
        """Remove a visit from the pool (it has been placed in a route)."""
        for col in self._column_items:
            for v in col.visits:
                if v.id == visit_id:
                    col.remove_visit(visit_id)
                    if not col.visits:
                        self.removeItem(col)
                        self._column_items.remove(col)
                    self._reposition()
                    return

    def set_font_size(self, size: int):
        if self._template_item:
            self._template_item.set_font_size(size)
        for col in self._column_items:
            col.set_font_size(size)
        self._reposition()

    def update_template(self, template: OfficeTemplate):
        if self._template_item:
            self._template_item._template = template
            self._template_item.update()

    def apply_filter(self, active_tags: set[str]):
        for col in self._column_items:
            col.apply_filter(active_tags)

    def highlight_pair(self, visit_id: Optional[int]):
        for col in self._column_items:
            col.highlight_pair(visit_id)

    def set_selected_visit(self, visit_id: Optional[int]):
        for col in self._column_items:
            col.set_selected_visit(visit_id)

    def move_column(self, street: str, direction: int):
        items = sorted(self._column_items,
                       key=lambda c: self._get_column_order(c.street))
        idx = next((i for i, c in enumerate(items) if c.street == street), None)
        if idx is None:
            return
        target = idx + direction
        if 0 <= target < len(items):
            # Swap positions
            items[idx]._display_order, items[target]._display_order = (
                getattr(items[target], "_display_order", target),
                getattr(items[idx], "_display_order", idx),
            )
        self._reposition(animate=True)

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
        col.visit_selected.connect(
            lambda c, v: self.visit_selected.emit(v.entry.visit_id or -1))

    def _reposition(self, animate: bool = False):
        fs = self._layout.font_size
        template_h = 0
        if self._template_item:
            self._template_item.setPos(0, 0)
            template_h = self._template_item.height() + _TEMPLATE_MARGIN

        items = sorted(self._column_items,
                       key=lambda c: self._get_column_order(c.street))
        x = 0
        for col in items:
            target = QPointF(x, template_h)
            if animate:
                self._layout._move_item(col, target, animate=True)
            else:
                col.setPos(target)
            x += col.column_width() + COLUMN_SPACING

        self.setSceneRect(self.itemsBoundingRect().adjusted(-10, -10, 10, 10))

    def _find_column_for_street(self, street: str) -> Optional[PoolColumnItem]:
        return next((c for c in self._column_items if c.street == street), None)

    def _get_column_order(self, street: str) -> int:
        col = self._find_column_for_street(street)
        return getattr(col, "_display_order", 999)
