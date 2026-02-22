"""Simple splash screen shown during early application startup."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QSplashScreen


class SplashScreen(QSplashScreen):
    """Lightweight splash screen with app title and loading text."""

    def __init__(self, app: QApplication):
        pixmap = QPixmap(420, 230)
        pixmap.fill(QColor("#fafafa"))

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        title_font = QFont("Segoe UI", 32, QFont.Weight.Bold)
        painter.setFont(title_font)
        painter.setPen(QColor("#323130"))
        title_rect = pixmap.rect()
        title_rect.setTop(55)
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignCenter, "Planeringsverktyg")

        subtitle_font = QFont("Segoe UI", 11)
        painter.setFont(subtitle_font)
        painter.setPen(QColor("#605e5c"))
        subtitle_rect = pixmap.rect()
        subtitle_rect.setTop(135)
        painter.drawText(
            subtitle_rect,
            Qt.AlignmentFlag.AlignCenter,
            "Hemtjänst",
        )

        loading_font = QFont("Segoe UI", 9)
        painter.setFont(loading_font)
        painter.setPen(QColor("#8a8886"))
        loading_rect = pixmap.rect()
        loading_rect.setBottom(pixmap.height() - 12)
        painter.drawText(
            loading_rect,
            Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignBottom,
            "Loading...",
        )
        painter.end()

        super().__init__(pixmap, Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowFlags(
            Qt.WindowType.SplashScreen
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint
        )

        screen = app.primaryScreen()
        if screen:
            screen_geometry = screen.availableGeometry()
            splash_geometry = self.geometry()
            splash_geometry.moveCenter(screen_geometry.center())
            self.move(splash_geometry.topLeft())
