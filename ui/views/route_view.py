"""RouteView – QGraphicsView for the routes (left) panel."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGraphicsView
from PySide6.QtGui import QPainter

from ui.scenes.route_scene import RouteScene


class RouteView(QGraphicsView):
    def __init__(self, scene: RouteScene, parent=None):
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
        self.setOptimizationFlag(
            QGraphicsView.OptimizationFlag.DontAdjustForAntialiasing, True
        )
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
