"""Application entry point."""

import os
import sys
from pathlib import Path


def _configure_webengine_for_network_path() -> None:
    app_dir = Path(__file__).resolve().parent
    if not str(app_dir).startswith("\\\\"):
        return

    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    current_flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").strip()
    if "--no-sandbox" not in current_flags:
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
            f"{current_flags} --no-sandbox".strip()
        )


def main():
    _configure_webengine_for_network_path()

    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt, QTimer
    from ui.splash_screen import SplashScreen
    from ui.app_style import apply_app_style

    # High-DPI support
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("Planeringsverktyg")
    app.setOrganizationName("Hemtjänst")
    apply_app_style(app)

    # Configure WebEngine after app is created
    from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineSettings
    profile = QWebEngineProfile.defaultProfile()
    profile.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
    profile.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)

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
