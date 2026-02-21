"""TravelStatusDialog – floating log of API requests/responses."""

from __future__ import annotations

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QTextEdit, QPushButton, QHBoxLayout, QLabel,
)
from PySide6.QtGui import QTextCursor, QColor, QTextCharFormat


class TravelStatusDialog(QDialog):
    """
    Non-modal window that shows a scrolling log of travel-time API
    requests and responses in real time.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("API-status – Resetid")
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowCloseButtonHint |
            Qt.WindowType.WindowMinimizeButtonHint
        )
        self.resize(560, 400)

        layout = QVBoxLayout(self)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(self._log.font())
        layout.addWidget(self._log)

        btn_row = QHBoxLayout()
        clear_btn = QPushButton("Rensa")
        clear_btn.clicked.connect(self._log.clear)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    @Slot(str)
    def log_request(self, text: str):
        self._append(f"→ {text}", "#1565C0")

    @Slot(str)
    def log_response(self, text: str):
        color = "#B71C1C" if "ERROR" in text else "#2E7D32"
        self._append(f"← {text}", color)

    @Slot(str, str, str, str)
    def log_error(self, from_addr: str, to_addr: str, mode: str, msg: str):
        self._append(
            f"  ✕ {from_addr} → {to_addr} [{mode}]: {msg}",
            "#C62828",
        )

    @Slot(int)
    def log_quota_warning(self, count: int):
        self._append(
            f"  ⚠ API-användning: {count} anrop (närmar sig gränsen!)",
            "#E65100",
        )

    def _append(self, text: str, hex_color: str):
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(hex_color))
        cursor = self._log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text + "\n", fmt)
        self._log.setTextCursor(cursor)
        self._log.ensureCursorVisible()
