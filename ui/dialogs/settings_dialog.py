"""Settings dialog – API key, default travel times, font size, minimum time."""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLineEdit, QSpinBox, QComboBox, QDialogButtonBox, QLabel,
    QTabWidget, QWidget, QCheckBox, QPushButton, QColorDialog,
)

from domain.models import Settings, TravelMode


class SettingsDialog(QDialog):
    _DEFAULT_VISIT_COLORS = {
        "blue": "#1F99CD",
        "green": "#3DB28D",
        "pink": "#EE229F",
        "red": "#D64545",
        "orange": "#F39C3D",
        "yellow": "#E6C84F",
        "black": "#2B2B2B",
    }

    def __init__(self, settings: Settings, api_key: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Inställningar")
        self.setMinimumWidth(420)
        self._settings = settings
        self._result_key = api_key

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        # --- Tab: API ---
        api_tab = QWidget()
        api_form = QFormLayout(api_tab)
        self._api_key_edit = QLineEdit(api_key)
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        api_form.addRow("API-nyckel (Routes API):", self._api_key_edit)

        usage_label = QLabel(
            f"Använt: {settings.api_usage_count} / {settings.api_usage_limit}"
        )
        api_form.addRow("API-användning:", usage_label)

        self._api_limit_spin = QSpinBox()
        self._api_limit_spin.setRange(1, 1_000_000)
        self._api_limit_spin.setValue(settings.api_usage_limit)
        api_form.addRow("Gräns för API-anrop:", self._api_limit_spin)

        tabs.addTab(api_tab, "API")

        # --- Tab: Resor ---
        travel_tab = QWidget()
        travel_form = QFormLayout(travel_tab)

        self._car_spin = QSpinBox()
        self._car_spin.setRange(1, 120)
        self._car_spin.setValue(settings.default_travel_car)
        self._car_spin.setSuffix(" min")
        travel_form.addRow("Standard restid (bil):", self._car_spin)

        self._bike_spin = QSpinBox()
        self._bike_spin.setRange(1, 120)
        self._bike_spin.setValue(settings.default_travel_bike)
        self._bike_spin.setSuffix(" min")
        travel_form.addRow("Standard restid (cykel):", self._bike_spin)

        self._walk_spin = QSpinBox()
        self._walk_spin.setRange(1, 120)
        self._walk_spin.setValue(settings.default_travel_walk)
        self._walk_spin.setSuffix(" min")
        travel_form.addRow("Standard restid (gång):", self._walk_spin)

        self._mode_combo = QComboBox()
        for mode, label in [(TravelMode.CAR, "Bil"),
                             (TravelMode.BIKE, "Cykel"),
                             (TravelMode.WALK, "Gång")]:
            self._mode_combo.addItem(label, mode)
        idx = self._mode_combo.findData(settings.default_travel_mode)
        if idx >= 0:
            self._mode_combo.setCurrentIndex(idx)
        travel_form.addRow("Standard färdsätt:", self._mode_combo)

        self._min_time_spin = QSpinBox()
        self._min_time_spin.setRange(0, 60)
        self._min_time_spin.setValue(settings.minimum_time_between_visits)
        self._min_time_spin.setSuffix(" min")
        travel_form.addRow("Minsta tid mellan besök:", self._min_time_spin)

        tabs.addTab(travel_tab, "Resor")

        # --- Tab: Visning ---
        view_tab = QWidget()
        view_form = QFormLayout(view_tab)

        self._font_spin = QSpinBox()
        self._font_spin.setRange(8, 24)
        self._font_spin.setValue(settings.font_size)
        self._font_spin.setSuffix(" pt")
        view_form.addRow("Textstorlek:", self._font_spin)

        self._debug_checkbox = QCheckBox("Aktivera debugläge")
        self._debug_checkbox.setChecked(bool(settings.debug_mode))
        view_form.addRow("Debug:", self._debug_checkbox)

        self._file_logging_checkbox = QCheckBox("Spara loggar till fil")
        self._file_logging_checkbox.setChecked(bool(settings.file_logging_enabled))
        view_form.addRow("Filloggning:", self._file_logging_checkbox)

        self._file_retention_spin = QSpinBox()
        self._file_retention_spin.setRange(1, 3650)
        self._file_retention_spin.setValue(max(1, int(settings.file_logging_retention_days)))
        self._file_retention_spin.setSuffix(" dagar")
        view_form.addRow("Logg-retention:", self._file_retention_spin)

        tabs.addTab(view_tab, "Visning")

        # --- Tab: Färger ---
        colors_tab = QWidget()
        colors_form = QFormLayout(colors_tab)
        self._color_edits: dict[str, QLineEdit] = {}

        self._add_color_row(colors_form, "Blå:", "blue", settings.visit_color_blue)
        self._add_color_row(colors_form, "Grön:", "green", settings.visit_color_green)
        self._add_color_row(colors_form, "Rosa:", "pink", settings.visit_color_pink)
        self._add_color_row(colors_form, "Röd:", "red", settings.visit_color_red)
        self._add_color_row(colors_form, "Orange:", "orange", settings.visit_color_orange)
        self._add_color_row(colors_form, "Gul:", "yellow", settings.visit_color_yellow)
        self._add_color_row(colors_form, "Svart:", "black", settings.visit_color_black)

        reset_row = QWidget()
        reset_layout = QHBoxLayout(reset_row)
        reset_layout.setContentsMargins(0, 0, 0, 0)
        reset_layout.setSpacing(6)
        reset_layout.addStretch(1)
        self._reset_colors_button = QPushButton("Återställ standardfärger")
        self._reset_colors_button.clicked.connect(self._reset_default_colors)
        reset_layout.addWidget(self._reset_colors_button)
        colors_form.addRow("", reset_row)

        tabs.addTab(colors_tab, "Färger")

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_api_key(self) -> str:
        return self._api_key_edit.text().strip()

    def _add_color_row(self, form: QFormLayout, label: str, key: str, value: str):
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)

        edit = QLineEdit(self._sanitize_hex(value))
        edit.setPlaceholderText("#RRGGBB")
        edit.setMaxLength(7)
        button = QPushButton("Välj…")
        button.clicked.connect(lambda: self._pick_color_for(key))

        row_layout.addWidget(edit)
        row_layout.addWidget(button)
        self._color_edits[key] = edit
        form.addRow(label, row)

    def _pick_color_for(self, key: str):
        edit = self._color_edits.get(key)
        if edit is None:
            return
        current = QColor(self._sanitize_hex(edit.text()))
        chosen = QColorDialog.getColor(current, self, "Välj färg")
        if chosen.isValid():
            edit.setText(chosen.name().upper())

    def _sanitize_hex(self, value: str, fallback: str = "#000000") -> str:
        text = (value or "").strip()
        if not text:
            return fallback
        if not text.startswith("#"):
            text = f"#{text}"
        if re.fullmatch(r"#[0-9A-Fa-f]{6}", text):
            return text.upper()
        return fallback

    def _reset_default_colors(self):
        for key, default in self._DEFAULT_VISIT_COLORS.items():
            edit = self._color_edits.get(key)
            if edit is not None:
                edit.setText(default)

    def get_settings(self) -> Settings:
        s = self._settings
        s.default_travel_car = self._car_spin.value()
        s.default_travel_bike = self._bike_spin.value()
        s.default_travel_walk = self._walk_spin.value()
        s.default_travel_mode = self._mode_combo.currentData()
        s.minimum_time_between_visits = self._min_time_spin.value()
        s.font_size = self._font_spin.value()
        s.api_usage_limit = self._api_limit_spin.value()
        s.debug_mode = self._debug_checkbox.isChecked()
        s.file_logging_enabled = self._file_logging_checkbox.isChecked()
        s.file_logging_retention_days = self._file_retention_spin.value()
        s.visit_color_blue = self._sanitize_hex(self._color_edits["blue"].text(), self._DEFAULT_VISIT_COLORS["blue"])
        s.visit_color_green = self._sanitize_hex(self._color_edits["green"].text(), self._DEFAULT_VISIT_COLORS["green"])
        s.visit_color_pink = self._sanitize_hex(self._color_edits["pink"].text(), self._DEFAULT_VISIT_COLORS["pink"])
        s.visit_color_red = self._sanitize_hex(self._color_edits["red"].text(), self._DEFAULT_VISIT_COLORS["red"])
        s.visit_color_orange = self._sanitize_hex(self._color_edits["orange"].text(), self._DEFAULT_VISIT_COLORS["orange"])
        s.visit_color_yellow = self._sanitize_hex(self._color_edits["yellow"].text(), self._DEFAULT_VISIT_COLORS["yellow"])
        s.visit_color_black = self._sanitize_hex(self._color_edits["black"].text(), self._DEFAULT_VISIT_COLORS["black"])
        return s
