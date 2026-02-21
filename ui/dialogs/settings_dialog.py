"""Settings dialog – API key, default travel times, font size, minimum time."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLineEdit, QSpinBox, QComboBox, QDialogButtonBox, QLabel,
    QTabWidget, QWidget,
)

from domain.models import Settings, TravelMode


class SettingsDialog(QDialog):
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

        tabs.addTab(view_tab, "Visning")

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_api_key(self) -> str:
        return self._api_key_edit.text().strip()

    def get_settings(self) -> Settings:
        s = self._settings
        s.default_travel_car = self._car_spin.value()
        s.default_travel_bike = self._bike_spin.value()
        s.default_travel_walk = self._walk_spin.value()
        s.default_travel_mode = self._mode_combo.currentData()
        s.minimum_time_between_visits = self._min_time_spin.value()
        s.font_size = self._font_spin.value()
        s.api_usage_limit = self._api_limit_spin.value()
        return s
