"""Application entry point."""

import sys

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QTimer

from ui.splash_screen import SplashScreen


def main():
    # High-DPI support
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("Planeringsverktyg")
    app.setOrganizationName("Hemtjänst")

    splash = SplashScreen(app)
    splash.show()
    for _ in range(3):
        app.processEvents()
    splash.raise_()
    splash.activateWindow()
    app.processEvents()

    from services.persistence_service import PersistenceService
    from ui.main_window import MainWindow

    db = PersistenceService()
    window = MainWindow(db)

    def finish_startup():
        window.show()
        app.processEvents()
        splash.finish(window)

    QTimer.singleShot(120, finish_startup)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
