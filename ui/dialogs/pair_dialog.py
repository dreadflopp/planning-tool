"""PairDialog – dialog for selecting a visit to pair with."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QPushButton, QDialogButtonBox, QMessageBox,
)
from PySide6.QtGui import QFont

from domain.models import Visit


class PairDialog(QDialog):
    """Dialog to select a visit to pair with the selected visit."""

    def __init__(self,
                 available_visits: list[Visit],
                 selected_visit: Optional[Visit] = None,
                 source_route_id: Optional[int] = None,
                 route_by_visit: Optional[dict[int, Optional[int]]] = None,
                 current_interval_by_visit: Optional[dict[int, Optional[str]]] = None,
                 recommended_visit_id: Optional[int] = None,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Para besök")
        self.resize(620, 560)
        self._visits = list(available_visits)
        self._selected_visit = selected_visit
        self._source_route_id = source_route_id
        self._route_by_visit = dict(route_by_visit or {})
        self._current_interval_by_visit = dict(current_interval_by_visit or {})
        self._recommended_visit_id = recommended_visit_id
        self._selected_id: Optional[int] = None
        self._ok_button: Optional[QPushButton] = None
        self._recommended_select_button: Optional[QPushButton] = None
        self._build_ui()

    def get_selected_visit_id(self) -> Optional[int]:
        """Return the ID of the selected visit to pair with."""
        return self._selected_id

    def _build_ui(self):
        layout = QVBoxLayout(self)

        self.setStyleSheet(
            "QDialog { color: #000000; }"
            "QLabel { color: #000000; }"
            "QListWidget { color: #000000; }"
            "QListWidget::item { color: #000000; }"
        )

        title = QLabel("Parning av besök")
        title_font = title.font()
        title_font.setBold(True)
        title_font.setPointSize(title_font.pointSize() + 1)
        title.setFont(title_font)
        layout.addWidget(title)

        selected_caption = QLabel("Valt besök:")
        selected_caption.setFont(QFont(selected_caption.font().family(), selected_caption.font().pointSize(), QFont.Weight.Bold))
        layout.addWidget(selected_caption)

        self._selected_info = QLabel(self._visit_details_text(self._selected_visit))
        self._selected_info.setWordWrap(True)
        layout.addWidget(self._selected_info)

        rec_caption = QLabel("Rekommenderad parning (samma regel som import):")
        rec_caption.setFont(QFont(rec_caption.font().family(), rec_caption.font().pointSize(), QFont.Weight.Bold))
        layout.addWidget(rec_caption)

        self._recommended_info = QLabel(self._recommended_details_text())
        self._recommended_info.setWordWrap(True)
        layout.addWidget(self._recommended_info)

        self._recommended_select_button = QPushButton("Välj rekommenderad")
        self._recommended_select_button.clicked.connect(self._select_recommended)
        layout.addWidget(self._recommended_select_button)

        others_caption = QLabel("Övriga möjliga objekt att para med (oparade):")
        others_caption.setFont(QFont(others_caption.font().family(), others_caption.font().pointSize(), QFont.Weight.Bold))
        layout.addWidget(others_caption)

        self._list_widget = QListWidget()
        self._list_widget.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self._list_widget)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok_button = button_box.button(QDialogButtonBox.StandardButton.Ok)
        if self._ok_button:
            self._ok_button.setEnabled(False)
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self._populate_list()
        self._refresh_recommended_button_state()

    def _populate_list(self):
        self._list_widget.clear()
        for visit in self._visits:
            if self._selected_visit and visit.id == self._selected_visit.id:
                continue
            if self._recommended_visit_id and visit.id == self._recommended_visit_id:
                continue
            item_text = self._visit_details_text(visit)
            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, visit.id)
            item.setToolTip(self._candidate_tooltip(visit))
            self._list_widget.addItem(item)

    def _visit_details_text(self, visit: Optional[Visit]) -> str:
        if not visit:
            return "-"
        default_interval = f"{visit.default_start}–{visit.default_end}"
        current_interval = self._current_interval_by_visit.get(visit.id) or "-"
        return (
            f"{visit.name}\n"
            f"Adress: {visit.address}\n"
            f"Standardtid: {default_interval}\n"
            f"Aktuell tid: {current_interval}"
        )

    def _recommended_details_text(self) -> str:
        if not self._recommended_visit_id:
            return "Ingen rekommenderad parning hittades."
        rec = next((v for v in self._visits if v.id == self._recommended_visit_id), None)
        if not rec:
            return "Ingen rekommenderad parning hittades."
        return self._visit_details_text(rec)

    def _candidate_tooltip(self, visit: Visit) -> str:
        route_id = self._route_by_visit.get(visit.id)
        if self._source_route_id is not None and route_id is not None and route_id == self._source_route_id:
            return "Varning: samma rutt. Dessa besök kan inte paras."
        return ""

    def _refresh_recommended_button_state(self):
        if self._recommended_select_button is not None:
            self._recommended_select_button.setEnabled(bool(self._recommended_visit_id))

    def _on_selection_changed(self):
        """Update selected visit ID when selection changes."""
        selected_items = self._list_widget.selectedItems()
        if selected_items:
            item = selected_items[0]
            self._selected_id = item.data(Qt.ItemDataRole.UserRole)
        else:
            self._selected_id = None
        if self._ok_button is not None:
            self._ok_button.setEnabled(self._selected_id is not None)

    def _select_recommended(self):
        if not self._recommended_visit_id:
            return
        self._selected_id = self._recommended_visit_id
        self._list_widget.clearSelection()
        if self._ok_button is not None:
            self._ok_button.setEnabled(True)
        self._on_accept()

    def _on_accept(self):
        if not self._selected_id:
            return
        target_route_id = self._route_by_visit.get(self._selected_id)
        if (self._source_route_id is not None and
                target_route_id is not None and
                target_route_id == self._source_route_id):
            QMessageBox.warning(
                self,
                "Kan inte para",
                "Besök på samma rutt kan inte paras.",
            )
            return
        self.accept()
