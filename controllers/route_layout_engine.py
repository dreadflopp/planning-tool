"""
RouteLayoutEngine
=================
Computes the y-positions of all items inside a RouteColumnItem or
PoolColumnItem and optionally animates them into place using
QVariantAnimation.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QVariantAnimation, QEasingCurve, Qt
from PySide6.QtWidgets import QGraphicsItem

from domain.constants import (
    VISIT_HEIGHT, TRAVEL_HEIGHT, EMPTY_HEIGHT, HEADER_HEIGHT,
)


def _scaled(base: int, font_size: int) -> int:
    """Scale a dimension proportionally to font size (base is for size 12)."""
    return max(int(base * font_size / 12), base // 2)


class RouteLayoutEngine:
    """
    Reusable utility that positions child items vertically inside a column.

    Usage::

        engine = RouteLayoutEngine(font_size=12)
        engine.layout_route_column(column_item, route, animate=True)
    """

    ANIMATE_DURATION_MS = 200

    def __init__(self, font_size: int = 12):
        self._font_size = font_size
        self._animations: list[QVariantAnimation] = []

    @property
    def font_size(self) -> int:
        return self._font_size

    @font_size.setter
    def font_size(self, value: int):
        self._font_size = value

    # ------------------------------------------------------------------
    # Scaled dimensions
    # ------------------------------------------------------------------

    def visit_height(self) -> int:
        return _scaled(VISIT_HEIGHT, self._font_size)

    def travel_height(self) -> int:
        return _scaled(TRAVEL_HEIGHT, self._font_size)

    def empty_height(self) -> int:
        return _scaled(EMPTY_HEIGHT, self._font_size)

    def header_height(self) -> int:
        return _scaled(HEADER_HEIGHT, self._font_size)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def layout_items(self, items: list, animate: bool = False) -> int:
        """
        Position a flat ordered list of QGraphicsItem-like objects at y=0
        relative to their parent, stacking them vertically.

        Each item must have:
          - setPos(x, y) method
          - height() method returning its current height in pixels

        Returns total height occupied.
        """
        y = 0
        for item in items:
            self._move_item(item, QPointF(0.0, float(y)), animate)
            y += item.height()
        return y

    def _move_item(self, item, target: QPointF, animate: bool):
        if not animate or item.pos() == target:
            item.setPos(target)
            return
        anim = QVariantAnimation()
        anim.setDuration(self.ANIMATE_DURATION_MS)
        anim.setStartValue(item.pos())
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.valueChanged.connect(lambda v, i=item: i.setPos(v))
        anim.finished.connect(lambda a=anim: self._animations.remove(a)
                               if a in self._animations else None)
        self._animations.append(anim)
        anim.start()

    def total_height_for_route_items(self, item_sequence: list) -> int:
        """Sum heights of all items in a sequence."""
        return sum(item.height() for item in item_sequence)
