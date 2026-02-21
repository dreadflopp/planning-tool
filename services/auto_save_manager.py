"""AutoSaveManager – batches rapid mutations into a single DB write."""

from PySide6.QtCore import QObject, QTimer, Signal


class AutoSaveManager(QObject):
    """
    Collects dirty route/setting IDs and flushes them to DB after a short
    delay (default 150 ms). Multiple rapid changes collapse into one write.
    """

    save_requested = Signal()  # connect to the actual save slot in main window

    def __init__(self, delay_ms: int = 150, parent=None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(delay_ms)
        self._timer.timeout.connect(self.save_requested)
        self._dirty_routes: set[int] = set()

    def mark_dirty(self, route_id: int | None = None):
        if route_id is not None:
            self._dirty_routes.add(route_id)
        if not self._timer.isActive():
            self._timer.start()

    def dirty_routes(self) -> set[int]:
        return set(self._dirty_routes)

    def clear(self):
        self._dirty_routes.clear()

    def flush_now(self):
        self._timer.stop()
        self.save_requested.emit()
