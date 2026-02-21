"""Application entry point."""

import sys
import os

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from services.persistence_service import PersistenceService
from ui.main_window import MainWindow


def main():
    # High-DPI support
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("Planeringsverktyg")
    app.setOrganizationName("Hemtjänst")

    db = PersistenceService()
    window = MainWindow(db)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
