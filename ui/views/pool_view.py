"""PoolView – QGraphicsView for the visit pool (right) panel."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGraphicsView
from PySide6.QtGui import QPainter

from ui.scenes.pool_scene import PoolScene


class PoolView(QGraphicsView):
    def __init__(self, scene: PoolScene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setAcceptDrops(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setViewportUpdateMode(
            QGraphicsView.ViewportUpdateMode.SmartViewportUpdate
        )
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
