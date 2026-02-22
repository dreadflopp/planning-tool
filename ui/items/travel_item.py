"""TravelItem – yellow travel-time block between two route entries."""

from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, Signal, QTimer
from PySide6.QtGui import QPainter, QPen, QColor, QFont
from PySide6.QtWidgets import QGraphicsObject, QGraphicsSceneMouseEvent

from domain.models import TravelSegment, TravelMode, TravelTimeState
from domain.constants import (
    VISIT_WIDTH, TRAVEL_HEIGHT,
    COLOR_TRAVEL_BG, COLOR_TRAVEL_BORDER,
)

_MODE_LABELS = {TravelMode.CAR: "Bil", TravelMode.BIKE: "Cykel", TravelMode.WALK: "Gång"}
_MODE_CYCLE = [TravelMode.CAR, TravelMode.BIKE, TravelMode.WALK]
_PAD = 6


class TravelItem(QGraphicsObject):
    """Yellow block showing travel duration and mode between two visits."""

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
        self._spinner_phase = 0
        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(260)
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

    def _on_spinner_tick(self):
        self._spinner_phase = (self._spinner_phase + 1) % 4
        self.update()

    def _sync_spinner(self):
        if self._seg.is_calculating:
            if not self._spinner_timer.isActive():
                self._spinner_timer.start()
        else:
            if self._spinner_timer.isActive():
                self._spinner_timer.stop()
            self._spinner_phase = 0

    def _title_rect(self) -> QRectF:
        w = self.width()
        h = self.height()
        return QRectF(_PAD, _PAD, w * 0.40, h * 0.44)

    def _retry_rect(self) -> QRectF:
        h = self.height()
        w = self.width()
        return QRectF(w * 0.66, h * 0.56, w * 0.3, h * 0.34)

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
        bg = QColor("#E8F5E9") if is_calc else QColor(COLOR_TRAVEL_BG)
        border_color = QColor("#2E7D32") if is_calc else QColor(COLOR_TRAVEL_BORDER)
        title_frame_color = QColor("#388E3C") if is_calc else QColor("#8D6E63")
        text_color = QColor("#1B5E20") if is_calc else QColor("#5D4037")
        value_color = QColor("#2E7D32") if is_calc else QColor("#1565C0")
        arrow_color = QColor("#2E7D32") if is_calc else QColor("#1565C0")
        arrow_hover_color = QColor("#66BB6A") if is_calc else QColor("#42A5F5")

        painter.fillRect(0, 0, w, h, bg)
        pen = QPen(border_color, 1)
        painter.setPen(pen)
        painter.drawRect(1, 1, w - 2, h - 2)

        title_rect = self._title_rect()
        painter.setPen(title_frame_color)
        painter.drawRoundedRect(title_rect, 4, 4)
        painter.setFont(QFont("Segoe UI", max(fs - 3, 8), QFont.Weight.Bold))
        painter.setPen(text_color)
        if self._is_edited_state():
            title = "Redigerad restid"
        elif self._is_calculated_state():
            title = "Verklig restid"
        else:
            title = "Standard restid"
        painter.drawText(
            title_rect.adjusted(4, 2, -4, -2),
            Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
            title,
        )

        if self._seg.is_calculating:
            dots = "." * self._spinner_phase
            painter.setPen(value_color)
            calc_rect = QRectF(_PAD, h * 0.64, w * 0.56, h * 0.28)
            painter.drawText(
                calc_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                f"Beräknar{dots}",
            )
        elif self._seg.api_failed:
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

        arrow_x = dur_x + dur_w + 2
        arrow_w = 16
        arrow_band_h = h * 0.64
        arrow_top = (h - arrow_band_h) / 2
        half = arrow_band_h / 2
        up_rect = QRectF(arrow_x, arrow_top, arrow_w, half)
        dn_rect = QRectF(arrow_x, arrow_top + half, arrow_w, half)
        painter.setFont(QFont("Segoe UI", max(fs - 3, 7)))
        painter.setPen(arrow_hover_color if self._hover_action == "dur_up" else arrow_color)
        painter.drawText(up_rect, Qt.AlignmentFlag.AlignCenter, "▲")
        painter.setPen(arrow_hover_color if self._hover_action == "dur_dn" else arrow_color)
        painter.drawText(dn_rect, Qt.AlignmentFlag.AlignCenter, "▼")

        # Mode button (right side, full height)
        mode_label = _MODE_LABELS.get(self._seg.mode, self._seg.mode)
        mode_x = w * 0.72
        mode_rect = QRectF(mode_x, _PAD, w - mode_x - _PAD, h - _PAD * 2)
        painter.setFont(QFont("Segoe UI", max(fs - 1, 8), QFont.Weight.Bold))
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
        h = self.height()
        w = self.width()
        if self._title_rect().contains(pos) and not self._seg.is_calculating:
            return "toggle_source"
        if self._seg.api_failed and self._retry_rect().contains(pos):
            return "retry"
        dur_x = w * 0.49
        dur_w = 34
        arrow_x = dur_x + dur_w + 2
        arrow_w = 16
        arrow_band_h = h * 0.64
        arrow_top = (h - arrow_band_h) / 2
        half = arrow_band_h / 2
        up_rect = QRectF(arrow_x, arrow_top, arrow_w, half)
        dn_rect = QRectF(arrow_x, arrow_top + half, arrow_w, half)
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
            w, h = self.width(), self.height()
            pos = event.pos()
            mode_x = w * 0.72

            action = self._hit_action(pos)
            if action == "dur_up":
                self.duration_up_requested.emit(self)
                return
            if action == "dur_dn":
                self.duration_down_requested.emit(self)
                return
            if action == "retry":
                self.retry_requested.emit(self)
                return
            if action == "toggle_source":
                self.source_toggle_requested.emit(self)
                return

            # Mode button click
            if pos.x() >= mode_x:
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
                self.duration_up_requested.emit(self)
                event.accept()
                return
            if action == "dur_dn":
                self.duration_down_requested.emit(self)
                event.accept()
                return
            if action == "retry":
                self.retry_requested.emit(self)
                event.accept()
                return
            if action == "toggle_source":
                self.source_toggle_requested.emit(self)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)
