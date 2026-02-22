"""TravelStatusDialog – floating log of API requests/responses."""

from __future__ import annotations

from datetime import datetime
import os
import sys

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
        self._log_dir = self._resolve_log_dir()
        self._file_logging_enabled = True
        self._file_logging_retention_days = 30
        self._ensure_log_dir_exists()
        self._cleanup_old_logs()

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
            f"  ⚠ API usage: {count} requests (approaching limit)",
            "#E65100",
        )

    @Slot(str)
    def log_debug(self, text: str):
        self._append(f"• {text}", "#6A1B9A")

    @Slot(str)
    def log_integrity_warning(self, text: str):
        self._append(f"  ⚠ {text}", "#C62828")

    @Slot(str)
    def log_integrity_ok(self, text: str):
        self._append(f"  ✓ {text}", "#2E7D32")

    @Slot(str)
    def log_integrity_change(self, text: str):
        self._append(f"  ↺ {text}", "#1565C0")

    def configure_file_logging(self, enabled: bool, retention_days: int):
        self._file_logging_enabled = bool(enabled)
        self._file_logging_retention_days = max(1, int(retention_days))
        self._ensure_log_dir_exists()
        self._cleanup_old_logs()

    def _append(self, text: str, hex_color: str):
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(hex_color))
        cursor = self._log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text + "\n", fmt)
        self._log.setTextCursor(cursor)
        self._log.ensureCursorVisible()
        self._append_to_file(text)

    def _resolve_log_dir(self) -> str:
        if getattr(sys, "frozen", False):
            base_dir = os.path.dirname(os.path.abspath(sys.executable))
        else:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(base_dir, "logs")

    def _ensure_log_dir_exists(self):
        try:
            os.makedirs(self._log_dir, exist_ok=True)
        except Exception:
            pass

    def _append_to_file(self, text: str):
        if not self._file_logging_enabled:
            return
        try:
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            filename = datetime.now().strftime("planning-tool-%Y-%m-%d.log")
            file_path = os.path.join(self._log_dir, filename)
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(f"[{stamp}] {text}\n")
        except Exception:
            # File logging must never break the UI logging path.
            pass

    def _cleanup_old_logs(self):
        try:
            now_ts = datetime.now().timestamp()
            max_age_seconds = max(1, int(self._file_logging_retention_days)) * 86400
            for name in os.listdir(self._log_dir):
                if not (name.startswith("planning-tool-") and name.endswith(".log")):
                    continue
                path = os.path.join(self._log_dir, name)
                if not os.path.isfile(path):
                    continue
                age_seconds = now_ts - os.path.getmtime(path)
                if age_seconds > max_age_seconds:
                    try:
                        os.remove(path)
                    except Exception:
                        pass
        except Exception:
            pass
