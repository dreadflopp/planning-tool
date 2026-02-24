"""Centralized Qt styling for consistent cross-platform widget appearance."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QStyleFactory


_APP_STYLESHEET = """
QWidget {
    font-size: 10pt;
}

QToolBar {
    spacing: 4px;
    padding: 2px;
    border: none;
}

QToolBar::separator {
    width: 1px;
    margin: 4px 6px;
    background: #C8CDD2;
}

QToolBar QToolButton,
QPushButton {
    min-height: 28px;
    padding: 4px 10px;
    border: 1px solid #B8BEC5;
    border-radius: 6px;
    background: #F7F8FA;
}

QToolBar QToolButton:hover,
QPushButton:hover {
    background: #EFF3F8;
    border-color: #A8AFB7;
}

QToolBar QToolButton:pressed,
QPushButton:pressed {
    background: #E5EBF2;
}

QToolBar QToolButton:checked,
QPushButton:checked {
    background: #DCEAF7;
    border-color: #8DAFD2;
}

QDialogButtonBox QPushButton {
    min-width: 92px;
}

QComboBox,
QLineEdit,
QSpinBox {
    min-height: 28px;
    padding: 2px 8px;
    border: 1px solid #B8BEC5;
    border-radius: 6px;
    background: #FFFFFF;
}

QComboBox:hover,
QLineEdit:hover,
QSpinBox:hover {
    border-color: #A8AFB7;
}

QComboBox:focus,
QLineEdit:focus,
QSpinBox:focus {
    border-color: #6EA1D4;
}

QTabBar::tab {
    min-height: 26px;
    min-width: 74px;
    padding: 4px 10px;
}
"""


def apply_app_style(app: QApplication) -> None:
    """Apply deterministic widget style and shared stylesheet."""
    if sys.platform.startswith("win"):
        app.setStyle(QStyleFactory.create("Fusion"))
    app.setStyleSheet(_APP_STYLESHEET)
