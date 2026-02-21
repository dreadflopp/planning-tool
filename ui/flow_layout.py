"""FlowLayout – a wrapping QLayout that flows children left-to-right, wrapping rows."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QSizePolicy


class FlowLayout(QLayout):
    """
    Lays out child widgets left-to-right, wrapping to the next row when the
    available width is exhausted.  heightForWidth is implemented so the parent
    layout correctly allocates vertical space.
    """

    def __init__(self, parent=None, h_spacing: int = 6, v_spacing: int = 4):
        super().__init__(parent)
        self._items: list = []
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing

    # ------------------------------------------------------------------
    # QLayout interface
    # ------------------------------------------------------------------

    def addItem(self, item):
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        eff = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = eff.x()
        y = eff.y()
        line_height = 0

        for item in self._items:
            w = item.sizeHint().width()
            h = item.sizeHint().height()
            next_x = x + w + self._h_spacing
            if next_x - self._h_spacing > eff.right() and line_height > 0:
                x = eff.x()
                y += line_height + self._v_spacing
                next_x = x + w + self._h_spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x = next_x
            line_height = max(line_height, h)

        return y + line_height - rect.y() + m.bottom()
