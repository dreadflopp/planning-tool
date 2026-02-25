"""Centralized Qt styling for consistent cross-platform widget appearance."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QStyleFactory


_APP_STYLESHEET = """
QWidget {
    font-size: 10pt;
    color: #1F2933;
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

QPushButton {
    min-height: 24px;
    padding: 2px 8px;
    border: 1px solid #BCC6D1;
    border-radius: 5px;
    background: #F4F7FA;
    color: #1F2933;
}

QPushButton:hover {
    background: #EAF1F8;
    border-color: #9EADBC;
}

QPushButton:pressed {
    background: #DEE7F1;
}

QPushButton:checked {
    background: #DBEAF9;
    border-color: #7EA3C8;
}

QDialogButtonBox QPushButton {
    min-width: 92px;
}

QComboBox,
QLineEdit,
QSpinBox {
    min-height: 24px;
    padding: 1px 6px;
    border: 1px solid #BCC6D1;
    border-radius: 5px;
    background: #FFFFFF;
    color: #1F2933;
}

QComboBox,
QSpinBox {
    padding-right: 20px;
}

QComboBox:hover,
QLineEdit:hover,
QSpinBox:hover {
    border-color: #9EADBC;
}

QComboBox:focus,
QLineEdit:focus,
QSpinBox:focus {
    border-color: #6B9CCB;
}

QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 20px;
    border-left: 1px solid #8FA1B4;
    background: #DDE6F0;
    border-top-right-radius: 4px;
    border-bottom-right-radius: 4px;
}

QComboBox::down-arrow {
    image: url(ui/assets/arrow_down_dark.svg);
    width: 10px;
    height: 6px;
}

QComboBox::drop-down:hover {
    background: #D3DEEA;
}

QComboBox::drop-down:pressed {
    background: #C9D6E5;
}

QAbstractSpinBox::up-button,
QAbstractSpinBox::down-button {
    width: 18px;
    border-left: 1px solid #8FA1B4;
    background: #DDE6F0;
    color: #1F2933;
}

QAbstractSpinBox::up-button:hover,
QAbstractSpinBox::down-button:hover {
    background: #D3DEEA;
}

QAbstractSpinBox::up-button:pressed,
QAbstractSpinBox::down-button:pressed {
    background: #C9D6E5;
}

QAbstractSpinBox::up-arrow {
    image: url(ui/assets/arrow_up_dark.svg);
    width: 10px;
    height: 6px;
}

QAbstractSpinBox::down-arrow {
    image: url(ui/assets/arrow_down_dark.svg);
    width: 10px;
    height: 6px;
}

QWidget#commandBar QToolButton {
    min-height: 24px;
    padding: 2px 8px;
    border: 1px solid #BCC6D1;
    border-radius: 5px;
    background: #F4F7FA;
    color: #1F2933;
}

QWidget#commandBar QToolButton:hover {
    background: #EAF1F8;
    border-color: #9EADBC;
}

QWidget#commandBar QToolButton:pressed {
    background: #DEE7F1;
}

QWidget#commandBar QToolButton:checked {
    background: #DBEAF9;
    border-color: #7EA3C8;
}

QWidget#filterBar QPushButton {
    min-height: 22px;
    padding: 1px 7px;
    border-radius: 10px;
}

QTabBar::tab {
    min-height: 24px;
    min-width: 74px;
    padding: 2px 8px;
}
"""


def apply_app_style(app: QApplication) -> None:
    """Apply deterministic widget style and shared stylesheet."""
    if sys.platform.startswith("win"):
        app.setStyle(QStyleFactory.create("Fusion"))
    app.setStyleSheet(_APP_STYLESHEET)
