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
    entry_color_change = Signal(object, object, object)  # (column_item, visit_item, color)
    entry_remove = Signal(object, object)
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
        return _scaled(VISIT_WIDTH, self._font_size)

    def header_height(self) -> int:
        from controllers.route_layout_engine import _scaled
        fs = self._font_size
        min_notes_h = _scaled(30, fs)
        notes_font = QFont("Segoe UI", max(fs - 2, 8))
        metrics = QFontMetrics(notes_font)
        notes_text = self._route.notes.strip() if self._route.notes else "Dubbelklicka här för anteckning"
        notes_w = max(80, self.column_width() - _PAD * 2 - 8)
        notes_h = metrics.boundingRect(
            0, 0, int(notes_w), 5000,
            int(Qt.TextFlag.TextWordWrap), notes_text,
        ).height() + 10
        return _TOP_ROW_H + max(min_notes_h, notes_h) + _PAD

    def refresh_header(self):
        self.prepareGeometryChange()
        self._layout_children()
        self.update()

    def _name_rect(self, w: float) -> QRectF:
        return QRectF(_PAD, _PAD, w - _BTN_W * 2 - _PAD * 3, _BTN_H)

    def _notes_rect(self, w: float, hh: float) -> QRectF:
        y = _TOP_ROW_H
        return QRectF(_PAD, y, w - _PAD * 2, hh - y - _PAD)

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
        """Grey out visits that do not contain any of the active_tags."""
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

        # Column background
        painter.fillRect(0, hh, w, h - hh, QColor(COLOR_ROUTE_COLUMN_BG))
        # Header background
        painter.fillRect(0, 0, w, hh, QColor(COLOR_HEADER_BG))
        painter.setPen(QPen(QColor("#B0BEC5"), 1))
        painter.drawRect(0, 0, w - 1, h - 1)

        # Route name (double-click editable)
        name_font = QFont("Segoe UI", fs + 1, QFont.Weight.Bold)
        painter.setFont(name_font)
        painter.setPen(QColor("#212121"))
        name_rect = self._name_rect(w)
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

        # Notes area (double-click editable)
        notes_rect = self._notes_rect(w, hh)
        painter.setPen(QPen(QColor("#B0BEC5"), 1))
        painter.setBrush(QBrush(QColor("#FFFDE7")))
        painter.drawRoundedRect(notes_rect, 4, 4)
        painter.setFont(QFont("Segoe UI", max(fs - 2, 8)))
        if self._route.notes.strip():
            painter.setPen(QColor("#37474F"))
            notes_text = self._route.notes
        else:
            painter.setPen(QColor("#90A4AE"))
            notes_text = "Dubbelklicka här för anteckning"
        painter.drawText(
            notes_rect.adjusted(4, 2, -4, -2),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
            notes_text,
        )

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent):
        event.accept()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent):
        pos = event.pos()
        w = self.column_width()
        hh = self.header_height()
        if pos.y() > hh:
            return super().mouseReleaseEvent(event)

        if event.button() == Qt.MouseButton.RightButton:
            menu = QMenu()
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
            vi = VisitItem(entry, self._font_size, in_route=True, parent=self)
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
                    self._connect_empty_item(ei)
                    self._empty_items.append(ei)
                    self._all_items.append(ei)
                    if old_positions is not None:
                        key = self._item_identity(ei)
                        ys = old_positions.get(key)
                        if ys:
                            ei.setPos(QPointF(0, ys.pop(0)))
                            positioned_item_ids.add(id(ei))
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
