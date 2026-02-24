"""TravelItem – travel-time block between route entries (default grey, calculated yellow)."""

from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, Signal, QTimer
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QFontMetrics
from PySide6.QtWidgets import QGraphicsObject, QGraphicsSceneMouseEvent

from domain.models import TravelSegment, TravelMode, TravelTimeState
from domain.constants import (
    VISIT_WIDTH, TRAVEL_HEIGHT,
    COLOR_TRAVEL_BG, COLOR_TRAVEL_BORDER,
    COLOR_TRAVEL_DEFAULT_BG, COLOR_TRAVEL_DEFAULT_BORDER,
)

_MODE_LABELS = {TravelMode.CAR: "Bil", TravelMode.BIKE: "Cykel", TravelMode.WALK: "Gå"}
_MODE_CYCLE = [TravelMode.CAR, TravelMode.BIKE, TravelMode.WALK]
_PAD = 1
_ARROW_BAND_FACTOR = 0.38
_ARROW_W = 12
_ARROW_GAP = 2
_BTN_H_FACTOR = 0.50
_TYPE_BTN_W_FACTOR = 0.39
_MODE_BTN_W_FACTOR = 0.15
_BTN_SIDE_INSET = 7


class TravelItem(QGraphicsObject):
    """Travel block showing travel duration and mode between two visits."""

    mode_changed = Signal(object)          # emits self (segment updated by caller)
    edit_minutes_requested = Signal(object)  # emits self
    duration_up_requested = Signal(object)
    duration_down_requested = Signal(object)
    retry_requested = Signal(object)
    source_toggle_requested = Signal(object)

    def __init__(self, segment: TravelSegment, font_size: int = 12, parent=None):
        super().__init__(parent)
        self._seg = segment
        self._font_size = font_size
        self._hover_action = None
        self._inconsistent = False
        self._spinner_phase = 0
        self._beep_anim_state = "enter" if segment.is_calculating else "idle"
        self._beep_anim_t = 0.0
        self._last_non_loading_is_calc = self._is_calculated_state()
        self._last_rendered_display_is_calc = self._last_non_loading_is_calc
        self._beep_bg_is_calc = self._last_non_loading_is_calc
        self._was_calculating = bool(segment.is_calculating)
        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(35)
        self._spinner_timer.timeout.connect(self._on_spinner_tick)
        self.setAcceptHoverEvents(True)
        self.setCacheMode(QGraphicsObject.CacheMode.DeviceCoordinateCache)
        self._sync_spinner()

    @property
    def segment(self) -> TravelSegment:
        return self._seg

    def set_font_size(self, size: int):
        self._font_size = size
        self.prepareGeometryChange()
        self.update()

    def set_inconsistent(self, inconsistent: bool):
        if self._inconsistent != inconsistent:
            self._inconsistent = inconsistent
            self.update()

    def _on_spinner_tick(self):
        self._spinner_phase = (self._spinner_phase + 1) % 100
        if self._beep_anim_state == "enter":
            self._beep_anim_t = min(1.0, self._beep_anim_t + 0.22)
            if self._beep_anim_t >= 1.0:
                self._beep_anim_state = "hold"
                self._beep_anim_t = 1.0
        elif self._beep_anim_state == "exit":
            self._beep_anim_t = min(1.0, self._beep_anim_t + 0.18)
            if self._beep_anim_t >= 1.0:
                self._beep_anim_state = "idle"
                self._beep_anim_t = 0.0
                if not self._seg.is_calculating:
                    self._spinner_timer.stop()
        self.update()

    def _sync_spinner(self):
        is_calculating = self._seg.is_calculating
        if is_calculating:
            if not self._was_calculating:
                self._beep_bg_is_calc = self._last_non_loading_is_calc
                self._beep_anim_state = "enter"
                self._beep_anim_t = 0.0
            elif self._beep_anim_state == "idle":
                self._beep_anim_state = "enter"
                self._beep_anim_t = 0.0
            if not self._spinner_timer.isActive():
                self._spinner_timer.start()
        else:
            if self._was_calculating and self._beep_anim_state in ("enter", "hold"):
                self._beep_anim_state = "exit"
                self._beep_anim_t = 0.0
            if self._beep_anim_state != "idle" and not self._spinner_timer.isActive():
                self._spinner_timer.start()
            if self._beep_anim_state == "idle":
                if self._spinner_timer.isActive() and not self._seg.is_calculating:
                    self._spinner_timer.stop()
                self._spinner_phase = 0
        self._was_calculating = is_calculating

    def _is_showing_beepboop(self) -> bool:
        return self._seg.is_calculating or self._beep_anim_state in ("enter", "hold", "exit")

    def _title_rect(self) -> QRectF:
        w = self.width()
        h = self.height()
        btn_h = h * _BTN_H_FACTOR
        btn_y = (h - btn_h) / 2
        return QRectF(_BTN_SIDE_INSET, btn_y, w * _TYPE_BTN_W_FACTOR, btn_h)

    def _retry_rect(self) -> QRectF:
        h = self.height()
        w = self.width()
        return QRectF(w * 0.66, h * 0.56, w * 0.3, h * 0.34)

    def _mode_rect(self) -> QRectF:
        w = self.width()
        h = self.height()
        btn_h = h * _BTN_H_FACTOR
        btn_y = (h - btn_h) / 2
        mode_w = w * _MODE_BTN_W_FACTOR
        mode_x = w - _BTN_SIDE_INSET - mode_w
        return QRectF(mode_x, btn_y, mode_w, btn_h)

    def _pin_loading_visual_state(self):
        pinned = (self._seg.loading_display_is_calc
                  if self._seg.loading_display_is_calc is not None
                  else self._last_rendered_display_is_calc)
        self._last_non_loading_is_calc = pinned
        self._beep_bg_is_calc = pinned

    def _draw_calculating_label(self, painter: QPainter, w: float, h: float, label_color: QColor):
        label = "BeepBoop"
        font = QFont("Segoe UI", max(self._font_size, 10), QFont.Weight.Bold)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        text_w = metrics.horizontalAdvance(label)
        center_x = (w - text_w) / 2
        start_x = -text_w - 16
        end_x = w + 16

        if self._beep_anim_state == "enter":
            t = self._beep_anim_t
            eased = 1 - (1 - t) ** 3
            x = start_x + (center_x - start_x) * eased
        elif self._beep_anim_state == "exit":
            t = self._beep_anim_t
            eased = t * t
            x = center_x + (end_x - center_x) * eased
        else:
            x = center_x

        painter.setPen(label_color)
        painter.drawText(QRectF(x, 0, text_w + 4, h),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         label)

    def _is_calculated_state(self) -> bool:
        return (
            self._seg.travel_time_state == TravelTimeState.CALCULATED
            and not self._seg.is_custom
            and not self._seg.api_failed
        )

    def _is_edited_state(self) -> bool:
        return self._seg.is_custom or self._seg.travel_time_state == TravelTimeState.EDITED

    def height(self) -> int:
        from controllers.route_layout_engine import _scaled
        return _scaled(TRAVEL_HEIGHT, self._font_size)

    def width(self) -> int:
        from controllers.route_layout_engine import _scaled
        return _scaled(VISIT_WIDTH, self._font_size)

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self.width(), self.height())

    def paint(self, painter: QPainter, option, widget=None):
        self._sync_spinner()
        w, h = self.width(), self.height()
        fs = self._font_size

        is_calc = self._is_calculated_state()
        showing_beep = self._is_showing_beepboop()
        if not showing_beep:
            self._last_non_loading_is_calc = is_calc
        if showing_beep and self._seg.loading_display_is_calc is not None:
            display_calc = bool(self._seg.loading_display_is_calc)
            self._beep_bg_is_calc = display_calc
        else:
            display_calc = self._beep_bg_is_calc if showing_beep else is_calc
        self._last_rendered_display_is_calc = display_calc
        bg = QColor(COLOR_TRAVEL_BG) if display_calc else QColor(COLOR_TRAVEL_DEFAULT_BG)
        border_color = QColor(COLOR_TRAVEL_BORDER) if display_calc else QColor(COLOR_TRAVEL_DEFAULT_BORDER)
        text_color = QColor("#5D4037") if display_calc else QColor("#424242")
        beep_label_color = QColor("#5D4037") if display_calc else QColor("#424242")
        value_color = QColor("#1565C0")
        arrow_color = QColor("#1565C0")
        arrow_hover_color = QColor("#42A5F5")

        painter.fillRect(0, 0, w, h, bg)
        pen = QPen(QColor("#C62828"), 2) if self._inconsistent else QPen(border_color, 1)
        painter.setPen(pen)
        painter.drawRect(1, 1, w - 2, h - 2)

        if showing_beep:
            self._draw_calculating_label(painter, w, h, beep_label_color)
            return

        title_rect = self._title_rect()
        painter.setPen(border_color)
        painter.drawRoundedRect(title_rect, 4, 4)
        painter.setFont(QFont("Segoe UI", max(fs - 3, 7), QFont.Weight.Bold))
        painter.setPen(text_color)
        if self._is_edited_state():
            title = "Redigerad restid"
        elif self._is_calculated_state():
            title = "Verklig restid"
        else:
            title = "Standard restid"
        title_font = painter.font()
        title_metrics = QFontMetrics(title_font)
        title_text = title_metrics.elidedText(
            title,
            Qt.TextElideMode.ElideRight,
            max(8, int(title_rect.width())),
        )
        painter.drawText(
            title_rect,
            Qt.AlignmentFlag.AlignCenter,
            title_text,
        )

        if self._seg.api_failed:
            painter.setPen(QColor("#B71C1C"))
            err_rect = QRectF(_PAD, h * 0.64, w * 0.56, h * 0.28)
            painter.drawText(
                err_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                "API-fel",
            )

        dur_x = w * 0.49
        dur_w = 34
        dur_rect = QRectF(dur_x, 0, dur_w, h)
        painter.setFont(QFont("Segoe UI", fs + 1, QFont.Weight.Bold))
        painter.setPen(value_color)
        painter.drawText(dur_rect,
                 Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                         f"{self._seg.travel_minutes}")

        arrow_x = dur_x + dur_w + 1
        arrow_w = _ARROW_W
        arrow_band_h = h * _ARROW_BAND_FACTOR
        arrow_top = (h - arrow_band_h) / 2
        half = max(1.0, (arrow_band_h - _ARROW_GAP) / 2)
        up_rect = QRectF(arrow_x, arrow_top, arrow_w, half)
        dn_rect = QRectF(arrow_x, arrow_top + half + _ARROW_GAP, arrow_w, half)
        painter.setFont(QFont("Segoe UI", max(fs - 5, 6)))
        painter.setPen(arrow_hover_color if self._hover_action == "dur_up" else arrow_color)
        painter.drawText(up_rect, Qt.AlignmentFlag.AlignCenter, "▲")
        painter.setPen(arrow_hover_color if self._hover_action == "dur_dn" else arrow_color)
        painter.drawText(dn_rect, Qt.AlignmentFlag.AlignCenter, "▼")

        # Mode button (right side)
        mode_label = _MODE_LABELS.get(self._seg.mode, self._seg.mode)
        mode_rect = self._mode_rect()
        painter.setFont(QFont("Segoe UI", max(fs - 3, 7), QFont.Weight.Bold))
        painter.setPen(border_color)
        painter.drawRoundedRect(mode_rect, 4, 4)
        painter.setPen(text_color)
        painter.drawText(mode_rect, Qt.AlignmentFlag.AlignCenter, mode_label)

        if self._seg.api_failed:
            painter.setFont(QFont("Segoe UI", max(fs - 3, 7), QFont.Weight.Bold))
            retry_rect = self._retry_rect()
            painter.setPen(QColor("#1565C0"))
            painter.drawRoundedRect(retry_rect, 3, 3)
            painter.drawText(retry_rect, Qt.AlignmentFlag.AlignCenter, "Försök igen")

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent):
        event.accept()

    def _hit_action(self, pos) -> str | None:
        if self._is_showing_beepboop():
            return None
        h = self.height()
        w = self.width()
        if self._title_rect().contains(pos) and not self._seg.is_calculating:
            return "toggle_source"
        if self._seg.api_failed and self._retry_rect().contains(pos):
            return "retry"
        dur_x = w * 0.49
        dur_w = 34
        arrow_x = dur_x + dur_w + 1
        arrow_w = _ARROW_W
        arrow_band_h = h * _ARROW_BAND_FACTOR
        arrow_top = (h - arrow_band_h) / 2
        half = max(1.0, (arrow_band_h - _ARROW_GAP) / 2)
        up_rect = QRectF(arrow_x, arrow_top, arrow_w, half)
        dn_rect = QRectF(arrow_x, arrow_top + half + _ARROW_GAP, arrow_w, half)
        if up_rect.contains(pos):
            return "dur_up"
        if dn_rect.contains(pos):
            return "dur_dn"
        return None

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

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._is_showing_beepboop():
                event.accept()
                return
            h = self.height()
            pos = event.pos()
            mode_rect = self._mode_rect()

            action = self._hit_action(pos)
            if action == "dur_up":
                self._pin_loading_visual_state()
                self.duration_up_requested.emit(self)
                return
            if action == "dur_dn":
                self._pin_loading_visual_state()
                self.duration_down_requested.emit(self)
                return
            if action == "retry":
                self._pin_loading_visual_state()
                self.retry_requested.emit(self)
                return
            if action == "toggle_source":
                self._pin_loading_visual_state()
                self.source_toggle_requested.emit(self)
                return

            # Mode button click
            if mode_rect.contains(pos):
                self._pin_loading_visual_state()
                idx = _MODE_CYCLE.index(self._seg.mode) if self._seg.mode in _MODE_CYCLE else 0
                self._seg.mode = _MODE_CYCLE[(idx + 1) % len(_MODE_CYCLE)]
                self.mode_changed.emit(self)
                self.update()
                return

            # Click on duration → edit
            if pos.y() <= h * 0.5:
                self.edit_minutes_requested.emit(self)

        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QGraphicsSceneMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            action = self._hit_action(event.pos())
            if action == "dur_up":
                self._pin_loading_visual_state()
                self.duration_up_requested.emit(self)
                event.accept()
                return
            if action == "dur_dn":
                self._pin_loading_visual_state()
                self.duration_down_requested.emit(self)
                event.accept()
                return
            if action == "retry":
                self._pin_loading_visual_state()
                self.retry_requested.emit(self)
                event.accept()
                return
            if action == "toggle_source":
                self._pin_loading_visual_state()
                self.source_toggle_requested.emit(self)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)
