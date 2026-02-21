"""TravelItem – yellow travel-time block between two route entries."""

from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QPainter, QPen, QColor, QFont
from PySide6.QtWidgets import QGraphicsObject, QGraphicsSceneMouseEvent

from domain.models import TravelSegment, TravelMode
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
    restore_calculated_requested = Signal(object)

    def __init__(self, segment: TravelSegment, font_size: int = 12, parent=None):
        super().__init__(parent)
        self._seg = segment
        self._font_size = font_size
        self.setCacheMode(QGraphicsObject.CacheMode.DeviceCoordinateCache)

    @property
    def segment(self) -> TravelSegment:
        return self._seg

    def set_font_size(self, size: int):
        self._font_size = size
        self.prepareGeometryChange()
        self.update()

    def height(self) -> int:
        from controllers.route_layout_engine import _scaled
        return _scaled(TRAVEL_HEIGHT, self._font_size)

    def width(self) -> int:
        from controllers.route_layout_engine import _scaled
        return _scaled(VISIT_WIDTH, self._font_size)

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self.width(), self.height())

    def paint(self, painter: QPainter, option, widget=None):
        w, h = self.width(), self.height()
        fs = self._font_size

        painter.fillRect(0, 0, w, h, QColor(COLOR_TRAVEL_BG))
        pen = QPen(QColor(COLOR_TRAVEL_BORDER), 1)
        painter.setPen(pen)
        painter.drawRect(1, 1, w - 2, h - 2)

        text_color = QColor("#5D4037")
        small = QFont("Segoe UI", max(fs - 2, 7))
        normal = QFont("Segoe UI", fs)

        # Time range
        painter.setFont(small)
        painter.setPen(text_color)
        time_str = (f"{self._seg.start_time} – {self._seg.end_time}  "
                    f"({self._seg.travel_minutes} min)")
        if self._seg.is_custom:
            time_str += " ✎"
        painter.drawText(QRectF(_PAD, _PAD, w * 0.65 - _PAD, h * 0.5),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         time_str)

        # Mode button (right side, full height)
        mode_label = _MODE_LABELS.get(self._seg.mode, self._seg.mode)
        mode_x = w * 0.65
        mode_rect = QRectF(mode_x, _PAD, w - mode_x - _PAD, h - _PAD * 2)
        painter.setFont(QFont("Segoe UI", max(fs - 1, 8), QFont.Weight.Bold))
        painter.setPen(QColor(COLOR_TRAVEL_BORDER))
        painter.drawRoundedRect(mode_rect, 4, 4)
        painter.setPen(QColor("#5D4037"))
        painter.drawText(mode_rect, Qt.AlignmentFlag.AlignCenter, mode_label)

        # Restore link (if custom)
        if self._seg.is_custom and self._seg.calculated_minutes is not None:
            painter.setFont(QFont("Segoe UI", max(fs - 3, 7)))
            painter.setPen(QColor("#1565C0"))
            restore_rect = QRectF(_PAD, h * 0.5, w * 0.6, h * 0.4)
            painter.drawText(restore_rect,
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             f"Återställ ({self._seg.calculated_minutes} min)")

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent):
        event.accept()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            w, h = self.width(), self.height()
            pos = event.pos()
            mode_x = w * 0.65

            # Mode button click
            if pos.x() >= mode_x:
                idx = _MODE_CYCLE.index(self._seg.mode) if self._seg.mode in _MODE_CYCLE else 0
                self._seg.mode = _MODE_CYCLE[(idx + 1) % len(_MODE_CYCLE)]
                self.mode_changed.emit(self)
                self.update()
                return

            # Restore link click
            if (self._seg.is_custom and self._seg.calculated_minutes is not None
                    and pos.y() > h * 0.5):
                self.restore_calculated_requested.emit(self)
                return

            # Click on duration → edit
            if pos.y() <= h * 0.5:
                self.edit_minutes_requested.emit(self)

        super().mouseReleaseEvent(event)
