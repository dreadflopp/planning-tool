"""VisitItem – QGraphicsObject that renders one care visit block."""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from PySide6.QtCore import Qt, QRectF, Signal, QPointF, QPoint, QMimeData, QVariantAnimation, QEasingCurve, QTimer
from PySide6.QtGui import (
    QPainter, QPen, QColor, QFont, QBrush, QDrag, QPixmap, QPainterPath, QFontMetrics,
)
from PySide6.QtWidgets import QGraphicsObject, QGraphicsSceneMouseEvent, QInputDialog
from PySide6.QtWidgets import QMenu

from domain.models import RouteEntry, Visit, VisitColor
from domain.constants import (
    VISIT_WIDTH, VISIT_HEIGHT,
    COLOR_VISIT_BG, COLOR_VISIT_BORDER,
    COLOR_VISIT_GREEN, COLOR_VISIT_PINK, COLOR_VISIT_BLUE,
    COLOR_VISIT_RED, COLOR_VISIT_ORANGE, COLOR_VISIT_YELLOW, COLOR_VISIT_BLACK,
    COLOR_PAIR_HIGHLIGHT, COLOR_GREYED_OUT,
    MIME_ROUTE_ENTRY, MIME_POOL_VISIT,
)

if TYPE_CHECKING:
    pass

_COLOR_HEX = {
    VisitColor.GREEN: COLOR_VISIT_GREEN,
    VisitColor.PINK: COLOR_VISIT_PINK,
    VisitColor.BLUE: COLOR_VISIT_BLUE,
    VisitColor.RED: COLOR_VISIT_RED,
    VisitColor.ORANGE: COLOR_VISIT_ORANGE,
    VisitColor.YELLOW: COLOR_VISIT_YELLOW,
    VisitColor.BLACK: COLOR_VISIT_BLACK,
}

_CONTEXT_COLORS = [
    ("Rosa", VisitColor.PINK),
    ("Blå", VisitColor.BLUE),
    ("Grön", VisitColor.GREEN),
    ("Röd", VisitColor.RED),
    ("Orange", VisitColor.ORANGE),
    ("Gul", VisitColor.YELLOW),
    ("Svart", VisitColor.BLACK),
    ("Ingen", None),
]

_COLOR_STRIP_W = 6
_PAD = 6
_COL2_W = 54
_COL3_W = 18
_COL4_W = 18


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
    color_change_requested = Signal(object, object)  # (self, color|None)
    remove_requested = Signal(object)     # emits self
    pair_requested = Signal(object)       # emits self
    unpair_requested = Signal(object)     # emits self
    _color_hex: dict[str, str] = dict(_COLOR_HEX)

    def __init__(self, entry: RouteEntry, font_size: int = 12,
                 in_route: bool = True, parent=None):
        super().__init__(parent)
        self._entry = entry
        self._font_size = font_size
        self._in_route = in_route
        self._greyed_out = False
        self._highlight_pair = False
        self._highlight_same_name_address = False
        self._selected = False
        self._inconsistent = False
        self._is_dragging = False
        self._pop_strength = 0.0
        self._pop_animation: Optional[QVariantAnimation] = None
        self._fade_animation: Optional[QVariantAnimation] = None
        self._pending_pop_bundle_height: Optional[int] = None
        self._hover_action: Optional[str] = None
        self._drag_start: Optional[QPointF] = None
        self.setAcceptHoverEvents(True)
        self.setCacheMode(QGraphicsObject.CacheMode.DeviceCoordinateCache)

    @classmethod
    def set_color_palette(cls, palette: dict[str, str]):
        merged = dict(_COLOR_HEX)
        for key, value in (palette or {}).items():
            color = QColor(str(value))
            if color.isValid():
                merged[str(key)] = color.name().upper()
        cls._color_hex = merged

    @classmethod
    def color_palette(cls) -> dict[str, str]:
        return dict(cls._color_hex)

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

    def set_same_name_address_highlight(self, highlighted: bool):
        if self._highlight_same_name_address != highlighted:
            self._highlight_same_name_address = highlighted
            self.update()

    def set_selected(self, selected: bool):
        if self._selected != selected:
            self._selected = selected
            self.update()

    def set_inconsistent(self, inconsistent: bool):
        if self._inconsistent != inconsistent:
            self._inconsistent = inconsistent
            self.update()

    def play_drop_pop(self, bundle_height: int = 0):
        if self.opacity() < 0.99:
            self._pending_pop_bundle_height = int(bundle_height)
            return
        if self._pop_animation is not None:
            self._pop_animation.stop()
        anim = QVariantAnimation(self)
        adaptive_duration = max(220, min(380, 180 + int(bundle_height * 0.9)))
        anim.setDuration(adaptive_duration)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.valueChanged.connect(self._on_pop_value_changed)
        anim.finished.connect(self._on_pop_finished)
        self._pop_animation = anim
        anim.start()

    def play_insert_fade(self, delay_ms: int = 0, duration_ms: int = 180):
        if self._fade_animation is not None:
            self._fade_animation.stop()
        self.setOpacity(0.0)

        def _start_fade():
            anim = QVariantAnimation(self)
            anim.setDuration(max(80, int(duration_ms)))
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.valueChanged.connect(lambda v: self.setOpacity(float(v)))
            anim.finished.connect(self._on_fade_finished)
            self._fade_animation = anim
            anim.start()

        if delay_ms > 0:
            QTimer.singleShot(int(delay_ms), _start_fade)
        else:
            _start_fade()

    def _text_col_width(self) -> int:
        w = self.width()
        controls_w = (_COL2_W + _COL3_W + (_COL4_W if self._in_route else 0))
        return max(80, w - controls_w - (_COLOR_STRIP_W + _PAD * 3))

    def _wrapped_height(self, text: str, font: QFont, width: int, min_h: int = 0) -> int:
        if not text:
            return min_h
        metrics = QFontMetrics(font)
        rect = metrics.boundingRect(0, 0, width, 500, int(Qt.TextFlag.TextWordWrap), text)
        return max(min_h, rect.height())

    def height(self) -> int:
        from controllers.route_layout_engine import _scaled
        base = _scaled(VISIT_HEIGHT, self._font_size)
        fs = self._font_size
        text_w = self._text_col_width()
        name_h = self._wrapped_height(self._entry.display_name, QFont("Segoe UI", fs, QFont.Weight.Bold), text_w, min_h=max(fs + 4, 16))
        ins_h = self._wrapped_height(self._entry.display_insatser, QFont("Segoe UI", max(fs - 3, 7)), text_w, min_h=0)
        dynamic = _PAD + name_h + max(fs + 6, 14) + max(fs + 6, 14) + (ins_h if ins_h > 0 else 0) + _PAD
        return max(base, dynamic)

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
        if self._is_dragging:
            bg = QColor("#E8F4FD")
        elif self._selected:
            bg = QColor("#E3F2FD")
        elif self._greyed_out:
            bg = QColor(COLOR_GREYED_OUT)
        else:
            bg = QColor(COLOR_VISIT_BG)
        painter.fillRect(0, 0, w, h, bg)

        # Color strip (left edge)
        color_key = self._entry.display_color
        if color_key and not self._greyed_out:
            strip_color = QColor(self._color_hex.get(color_key, COLOR_VISIT_BG))
            painter.fillRect(0, 0, _COLOR_STRIP_W, h, strip_color)

        # Border: selection > pair highlight > same-name-address > normal
        if self._inconsistent:
            pen = QPen(QColor("#C62828"), 3)
        elif self._is_dragging:
            pen = QPen(QColor("#42A5F5"), 2, Qt.PenStyle.DashLine)
        elif self._selected:
            pen = QPen(QColor("#1565C0"), 3)
        elif self._highlight_pair:
            pen = QPen(QColor(COLOR_PAIR_HIGHLIGHT), 3)  # orange
        elif self._highlight_same_name_address:
            pen = QPen(QColor("#1E88E5"), 2)  # blue
        else:
            pen = QPen(QColor(COLOR_VISIT_BORDER), 1)
        painter.setPen(pen)
        painter.drawRect(1, 1, w - 2, h - 2)

        if self._pop_strength > 0.0:
            alpha = int(110 * self._pop_strength)
            painter.fillRect(2, 2, w - 4, h - 4, QColor(66, 165, 245, alpha))
            pop_pen = QPen(QColor(21, 101, 192, int(170 * self._pop_strength)), 2)
            painter.setPen(pop_pen)
            painter.drawRect(1, 1, w - 2, h - 2)

        text_color = QColor("#888888" if self._greyed_out else "#212121")

        col1_x = _COLOR_STRIP_W + _PAD
        control_w = _COL2_W + _COL3_W + (_COL4_W if self._in_route else 0)
        col2_x = w - control_w - _PAD
        col3_x = col2_x + _COL2_W
        col4_x = col3_x + _COL3_W
        text_w = max(80, col2_x - col1_x - _PAD)
        line_h = max(fs + 6, 14)
        y = _PAD

        # --- Column 1 ---
        name_font = QFont("Segoe UI", fs, QFont.Weight.Bold)
        painter.setFont(name_font)
        painter.setPen(text_color)
        name_h = self._wrapped_height(self._entry.display_name, name_font, text_w, min_h=line_h)
        name_rect = QRectF(col1_x, y, text_w, name_h)
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap,
                         self._entry.display_name)
        y += name_h

        small_font = QFont("Segoe UI", max(fs - 2, 7), QFont.Weight.DemiBold)
        painter.setFont(small_font)

        from controllers.route_recalculation_engine import _display_time
        time_str = f"{_display_time(self._entry.start_time)} – {_display_time(self._entry.end_time)}"
        time_rect = QRectF(col1_x, y, text_w, line_h)
        painter.drawText(time_rect,
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         time_str)
        y += line_h

        addr_rect = QRectF(col1_x, y, text_w, line_h)
        painter.drawText(addr_rect,
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         self._entry.display_address)
        y += line_h

        ins = self._entry.display_insatser
        if ins:
            ins_font = QFont("Segoe UI", max(fs - 3, 7))
            painter.setFont(ins_font)
            ins_rect = QRectF(col1_x, y, text_w, h - y - _PAD)
            painter.drawText(ins_rect,
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
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
                # Duration arrows ▲▼ (to the right of duration)
                arrow_font = QFont("Segoe UI", max(fs - 3, 7))
                painter.setFont(arrow_font)
                up_rect, dn_rect = self._duration_arrow_rects(h, col3_x)
                up_col = QColor("#42A5F5") if self._hover_action == "dur_up" else QColor("#1565C0")
                dn_col = QColor("#42A5F5") if self._hover_action == "dur_dn" else QColor("#1565C0")
                painter.setPen(up_col)
                painter.drawText(up_rect, Qt.AlignmentFlag.AlignCenter, "▲")
                painter.setPen(dn_col)
                painter.drawText(dn_rect, Qt.AlignmentFlag.AlignCenter, "▼")

                # --- Column 3: position arrows ---
                pos_up_rect = QRectF(col4_x, _PAD, _COL4_W, h * 0.5 - _PAD)
                pos_dn_rect = QRectF(col4_x, h * 0.5, _COL4_W, h * 0.5 - _PAD)
                painter.setFont(arrow_font)
                painter.setPen(QColor("#78909C") if self._hover_action == "pos_up" else QColor("#555555"))
                painter.drawText(pos_up_rect, Qt.AlignmentFlag.AlignCenter, "↑")
                painter.setPen(QColor("#78909C") if self._hover_action == "pos_dn" else QColor("#555555"))
                painter.drawText(pos_dn_rect, Qt.AlignmentFlag.AlignCenter, "↓")

    def _duration_arrow_rects(self, h: int, col3_x: float) -> tuple[QRectF, QRectF]:
        top_h = h * 0.5 - _PAD
        half = top_h / 2
        up_rect = QRectF(col3_x, _PAD, _COL3_W, half)
        dn_rect = QRectF(col3_x, _PAD + half, _COL3_W, half)
        return up_rect, dn_rect

    def _hit_action(self, pos: QPointF) -> Optional[str]:
        if not self._in_route:
            return None
        w, h = self.width(), self.height()
        col2_x = w - (_COL2_W + _COL3_W + _COL4_W) - _PAD
        col3_x = col2_x + _COL2_W
        col4_x = col3_x + _COL3_W
        up_rect, dn_rect = self._duration_arrow_rects(h, col3_x)
        pos_up_rect = QRectF(col4_x, _PAD, _COL4_W, h * 0.5 - _PAD)
        pos_dn_rect = QRectF(col4_x, h * 0.5, _COL4_W, h * 0.5 - _PAD)
        if up_rect.contains(pos):
            return "dur_up"
        if dn_rect.contains(pos):
            return "dur_dn"
        if pos_up_rect.contains(pos):
            return "pos_up"
        if pos_dn_rect.contains(pos):
            return "pos_dn"
        return None

    def _is_time_interval_hit(self, pos: QPointF) -> bool:
        if not self._in_route:
            return False
        w, h = self.width(), self.height()
        col1_x = _COLOR_STRIP_W + _PAD
        col2_x = w - (_COL2_W + _COL3_W + _COL4_W) - _PAD
        return (col1_x <= pos.x() <= col2_x and
                h * 0.28 <= pos.y() <= h * 0.50)

    def hoverMoveEvent(self, event):
        new_action = self._hit_action(event.pos())
        if new_action != self._hover_action:
            self._hover_action = new_action
            self.update()
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event):
        if self._hover_action is not None:
            self._hover_action = None
            self.update()
        super().hoverLeaveEvent(event)

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
        if event.button() == Qt.MouseButton.RightButton:
            menu = QMenu()
            color_menu = menu.addMenu("Färg")
            for label, color_value in _CONTEXT_COLORS:
                act = color_menu.addAction(label)
                if (self._entry.display_color or None) == color_value:
                    act.setCheckable(True)
                    act.setChecked(True)
                act.triggered.connect(
                    lambda _checked=False, c=color_value: self.color_change_requested.emit(self, c)
                )
            visit = self._entry.visit
            visit_id = self._entry.visit_id
            is_paired = bool(visit_id and visit and visit.pair_partner_id)
            if visit_id:
                menu.addSeparator()
                if is_paired:
                    unpair_action = menu.addAction("Avpara")
                    unpair_action.triggered.connect(lambda: self.unpair_requested.emit(self))
                else:
                    pair_action = menu.addAction("Para")
                    pair_action.triggered.connect(lambda: self.pair_requested.emit(self))
            menu.addSeparator()
            remove_action = menu.addAction("Ta bort besök")
            remove_action.triggered.connect(lambda: self.remove_requested.emit(self))
            menu.exec(event.screenPos())
            event.accept()
            return

        self._drag_start = None
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.pos()
            if self._in_route:
                action = self._hit_action(pos)
                if action == "dur_up":
                    self.duration_up_requested.emit(self)
                    return
                if action == "dur_dn":
                    self.duration_down_requested.emit(self)
                    return
                if action == "pos_up":
                    self.move_up_requested.emit(self)
                    return
                if action == "pos_dn":
                    self.move_down_requested.emit(self)
                    return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QGraphicsSceneMouseEvent):
        if self._in_route:
            pos = event.pos()
            action = self._hit_action(pos)
            if action == "dur_up":
                self.duration_up_requested.emit(self)
                event.accept()
                return
            if action == "dur_dn":
                self.duration_down_requested.emit(self)
                event.accept()
                return
            if action == "pos_up":
                self.move_up_requested.emit(self)
                event.accept()
                return
            if action == "pos_dn":
                self.move_down_requested.emit(self)
                event.accept()
                return
            if self._is_time_interval_hit(pos):
                self.time_edit_requested.emit(self)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    # ------------------------------------------------------------------
    # Drag
    # ------------------------------------------------------------------

    def _start_drag(self, event: QGraphicsSceneMouseEvent):
        import json as _json
        from PySide6.QtWidgets import QGraphicsRectItem

        self._is_dragging = True
        self.setOpacity(0.32)
        self.update()

        # Show ghost placeholder in original position while dragging
        scene = self.scene()
        ghost = None
        if scene:
            ghost = QGraphicsRectItem(QRectF(0, 0, self.width(), self.height()))
            ghost.setPos(self.mapToScene(QPointF(0, 0)))
            ghost.setBrush(QBrush(QColor(227, 242, 253, 120)))
            ghost.setPen(QPen(QColor("#42A5F5"), 2, Qt.PenStyle.DashLine))
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
        item_pix = QPixmap(self.width(), self.height())
        item_pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(item_pix)
        self.paint(p, None)
        p.end()

        preview = QPixmap(self.width() + 12, self.height() + 12)
        preview.fill(Qt.GlobalColor.transparent)
        pp = QPainter(preview)
        pp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pp.setOpacity(0.24)
        pp.drawPixmap(6, 6, item_pix)
        pp.setOpacity(1.0)
        pp.drawPixmap(2, 2, item_pix)
        pp.end()

        drag.setPixmap(preview)
        drag.setHotSpot(event.pos().toPoint() + QPoint(2, 2))

        try:
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            # Remove ghost after drag completes
            if ghost and ghost.scene():
                ghost.scene().removeItem(ghost)
            self._is_dragging = False
            self.setOpacity(1.0)
            self.update()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _hm_to_min(hhmm: str) -> int:
        from controllers.route_recalculation_engine import _t2m
        return _t2m(hhmm)

    def _on_pop_value_changed(self, value):
        self._pop_strength = float(value)
        self.update()

    def _on_pop_finished(self):
        self._pop_strength = 0.0
        self._pop_animation = None
        self.update()

    def _on_fade_finished(self):
        self.setOpacity(1.0)
        self._fade_animation = None
        if self._pending_pop_bundle_height is not None:
            pending = self._pending_pop_bundle_height
            self._pending_pop_bundle_height = None
            self.play_drop_pop(pending)
