"""MainWindow – top-level coordinator for the route planning application."""

from __future__ import annotations

import configparser
import functools
import inspect
import json
import os
import re
from datetime import datetime
from typing import Optional

from PySide6.QtCore import Qt, Slot, QTimer, QRectF
from PySide6.QtGui import QAction, QColor, QFont, QPageLayout, QPageSize, QPainter, QPdfWriter, QPen
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QToolBar, QLabel, QSpinBox, QComboBox, QPushButton,
    QScrollArea, QFileDialog, QMessageBox, QInputDialog, QDialog,
    QGroupBox, QScrollBar, QSizePolicy, QFormLayout, QDialogButtonBox, QLineEdit,
)

from domain.models import (
    Route, RouteEntry, TravelSegment, EmptySpace, Visit,
    Settings, TravelMode, TravelTimeState, ExtraTimeBlock,
)
from domain.constants import (
    VISIT_WIDTH, COLUMN_SPACING,
    COLOR_HEADER_BG, COLOR_ROUTE_COLUMN_BG, COLOR_VISIT_BG, COLOR_VISIT_BORDER,
    COLOR_TRAVEL_BG, COLOR_TRAVEL_BORDER, COLOR_EMPTY_BG, COLOR_EMPTY_BORDER,
)
from services.persistence_service import PersistenceService
from services.excel_import_service import ExcelImportService
from services.map_geocoding_service import MapGeocodingService
from services.travel_time_service import TravelTimeService
from services.auto_save_manager import AutoSaveManager
from controllers.route_recalculation_engine import RouteRecalculationEngine, _t2m, _m2t, _display_time
from controllers.route_layout_engine import RouteLayoutEngine
from ui.scenes.route_scene import RouteScene
from ui.scenes.pool_scene import PoolScene
from ui.views.route_view import RouteView
from ui.views.pool_view import PoolView
from ui.flow_layout import FlowLayout
from ui.dialogs.settings_dialog import SettingsDialog
from ui.dialogs.travel_status_dialog import TravelStatusDialog
from ui.items.visit_item import VisitItem
from ui.visit_map_window import VisitMapWindow


_SETTINGS_FILE = "settings.ini"


def _load_api_key() -> str:
    cfg = configparser.ConfigParser()
    cfg.read(_SETTINGS_FILE)
    return cfg.get("api", "key", fallback="")


def _save_api_key(key: str):
    cfg = configparser.ConfigParser()
    cfg.read(_SETTINGS_FILE)
    if "api" not in cfg:
        cfg["api"] = {}
    cfg["api"]["key"] = key
    with open(_SETTINGS_FILE, "w") as f:
        cfg.write(f)


class MainWindow(QMainWindow):
    def __init__(self, persistence: PersistenceService):
        super().__init__()
        self.setWindowTitle("Planeringsverktyg – Hemtjänst")
        self.resize(1400, 900)

        self._db = persistence
        self._settings: Settings = self._db.load_settings()
        self._apply_visit_color_palette()
        self._api_key: str = _load_api_key()
        self._default_template_address = "Angereds Torg 5, 424 65 Angered"
        self._default_templates = [
            {"name": "Kontor", "address": self._default_template_address, "duration_minutes": 10},
            {"name": "Uppstart", "address": self._default_template_address, "duration_minutes": 30},
            {"name": "Rast", "address": self._default_template_address, "duration_minutes": 40},
            {"name": "Avslut", "address": self._default_template_address, "duration_minutes": 20},
            {"type": "extra_time", "duration_minutes": self._settings.extra_time_minutes},
        ]

        # In-memory collections
        self._visits: dict[int, Visit] = {}
        self._routes: dict[int, Route] = {}

        # Services / controllers
        self._travel_svc = TravelTimeService(self._db, self._api_key, self._settings)
        self._recalc = RouteRecalculationEngine(self._db, self._travel_svc, self._settings)
        self._layout_engine = RouteLayoutEngine(self._settings.font_size)
        self._autosave = AutoSaveManager(parent=self)
        self._autosave.save_requested.connect(self._on_autosave)

        # Import / travel status helpers
        self._import_svc = ExcelImportService(self._db)
        self._travel_status: Optional[TravelStatusDialog] = None
        self._travel_debounce_timers: dict[tuple[int, int, int], QTimer] = {}
        self._failed_fallback_keys: set[tuple[str, str, str]] = set()
        self._map_window: Optional[VisitMapWindow] = None
        self._selected_visit_id: Optional[int] = None

        # Paired visit index: visit_id → partner_visit_id
        self._pairs: dict[int, int] = {}
        self._debug_action_depth = 0
        self._debug_wrappers_installed = False
        self._bootstrap_complete = False

        self._install_debug_action_wrappers()

        # Build UI
        self._build_ui()
        self._load_data()
        self._connect_travel_signals()
        self._bootstrap_complete = True
        if self._settings.debug_mode:
            self._run_debug_integrity_scan("Init")

    def _install_debug_action_wrappers(self):
        if self._debug_wrappers_installed:
            return

        action_methods = {
            "_on_filter_changed",
            "_on_add_route",
            "_on_route_renamed",
            "_on_route_notes_changed",
            "_on_route_delete",
            "_on_entry_dropped",
            "_on_entry_returned",
            "_on_extra_time_remove",
            "_on_entry_color_changed",
            "_on_entry_remove_requested",
            "_on_entry_moved",
            "_on_entry_time_edit",
            "_on_entry_duration_changed",
            "_on_empty_remove",
            "_on_travel_mode_changed",
            "_on_travel_duration_changed",
            "_on_travel_minutes_edit",
            "_on_travel_retry",
            "_on_travel_source_toggle",
            "_on_time_integrity",
            "_on_block_visibility_changed",
            "_on_extra_time_auto_toggled",
            "_on_extra_time_minutes_changed",
            "_on_reset_all",
            "_on_default_mode_changed",
            "_on_font_size_changed",
            "_on_import_excel",
            "_on_open_settings",
            "_on_export_state",
            "_on_import_state",
            "_on_export_excel",
            "_on_export_pdf",
            "_on_open_map",
        }

        for name in action_methods:
            fn = getattr(self, name, None)
            if not callable(fn):
                continue

            sig = inspect.signature(fn)
            positional_params = [
                p for p in sig.parameters.values()
                if p.kind in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                )
            ]
            has_var_positional = any(
                p.kind == inspect.Parameter.VAR_POSITIONAL
                for p in sig.parameters.values()
            )
            max_positional = len(positional_params)

            @functools.wraps(fn)
            def _wrapped(*args,
                         __fn=fn,
                         __name=name,
                         __has_var_positional=has_var_positional,
                         __max_positional=max_positional,
                         **kwargs):
                call_args = args if __has_var_positional else args[:__max_positional]
                self._debug_action_depth += 1
                try:
                    result = __fn(*call_args, **kwargs)
                finally:
                    self._debug_action_depth = max(0, self._debug_action_depth - 1)
                if self._debug_action_depth == 0:
                    self._on_debug_post_action(__name, call_args, kwargs)
                return result

            setattr(self, name, _wrapped)

        self._debug_wrappers_installed = True

    def _on_debug_post_action(self, action_name: str, args: tuple, kwargs: dict):
        if not self._settings.debug_mode:
            return
        if not self._bootstrap_complete:
            return
        stamp = datetime.now().strftime("%H:%M:%S")
        args_text = self._format_debug_args(args, kwargs)
        self._get_travel_status().log_debug(
            f"[{stamp}] {action_name}{args_text}"
        )
        self._run_debug_integrity_scan(action_name)

    def _format_debug_args(self, args: tuple, kwargs: dict) -> str:
        parts: list[str] = []
        for arg in args:
            text = repr(arg)
            if len(text) > 80:
                text = text[:77] + "..."
            parts.append(text)
        for k, v in kwargs.items():
            text = repr(v)
            if len(text) > 80:
                text = text[:77] + "..."
            parts.append(f"{k}={text}")
        return f"({', '.join(parts)})" if parts else "()"

    def _run_debug_integrity_scan(self, trigger: str, log_result: bool = True):
        by_route, travel_by_route, empty_by_route, issues = self._scan_time_inconsistencies()
        self._route_scene.set_inconsistent_entries(by_route)
        self._route_scene.set_inconsistent_blocks(travel_by_route, empty_by_route)
        if not log_result:
            return
        if issues:
            self._get_travel_status().log_integrity_warning(
                f"{trigger}: {len(issues)} time anomalies"
            )
            for issue in issues[:50]:
                self._get_travel_status().log_integrity_warning(issue)
            if len(issues) > 50:
                self._get_travel_status().log_integrity_warning(
                    f"... and {len(issues) - 50} more"
                )
        else:
            self._get_travel_status().log_integrity_ok(f"{trigger}: no time anomalies")

    def _scan_time_inconsistencies(self) -> tuple[
        dict[int, set[int]],
        dict[int, set[tuple[int, int]]],
        dict[int, set[tuple[int, int]]],
        list[str],
    ]:
        inconsistent_by_route: dict[int, set[int]] = {}
        inconsistent_travel_by_route: dict[int, set[tuple[int, int]]] = {}
        inconsistent_empty_by_route: dict[int, set[tuple[int, int]]] = {}
        issues: list[str] = []

        for route in sorted(self._routes.values(), key=lambda r: r.display_order):
            bad_entries: set[int] = set()
            bad_travel_pairs: set[tuple[int, int]] = set()
            bad_empty_pairs: set[tuple[int, int]] = set()
            entries = route.sorted_entries()

            for entry in entries:
                start_m = _t2m(entry.start_time)
                end_m = _t2m(entry.end_time)
                if end_m <= start_m:
                    if entry.id is not None:
                        bad_entries.add(entry.id)
                    issues.append(
                        f"{route.name}: invalid duration for '{entry.display_name}' ({_display_time(entry.start_time)}–{_display_time(entry.end_time)})"
                    )

            for idx in range(1, len(entries)):
                prev = entries[idx - 1]
                curr = entries[idx]
                seg = route.travel_segment_between(prev.id, curr.id)
                esp = route.empty_space_between(prev.id, curr.id)
                extra = route.extra_time_for_entry(curr.id)

                if seg is not None and seg.is_calculating:
                    # Ignore transient state while API travel update is in flight.
                    continue

                travel_min = max(0, seg.travel_minutes) if seg else 0
                empty_min = max(0, esp.duration_minutes) if esp else 0
                extra_min = self._settings.extra_time_minutes if extra else 0

                expected_start = _t2m(prev.end_time) + travel_min + extra_min + empty_min
                actual_start = _t2m(curr.start_time)

                if actual_start != expected_start:
                    pair_key = (prev.id, curr.id)
                    if prev.id is not None:
                        bad_entries.add(prev.id)
                    if curr.id is not None:
                        bad_entries.add(curr.id)
                    bad_travel_pairs.add(pair_key)
                    bad_empty_pairs.add(pair_key)
                    issues.append(
                        f"{route.name}: '{curr.display_name}' starts at { _display_time(curr.start_time) } but expected { _display_time(_m2t(expected_start)) }"
                    )

                if seg is not None:
                    # TravelSegment start/end are runtime display fields and may be
                    # blank right after app startup before recalculation.
                    # Only validate these fields when both are populated.
                    if seg.start_time and seg.end_time:
                        expected_seg_start = _t2m(prev.end_time)
                        expected_seg_end = expected_seg_start + travel_min
                        seg_start = _t2m(seg.start_time)
                        seg_end = _t2m(seg.end_time)
                        if seg_start != expected_seg_start or seg_end != expected_seg_end:
                            pair_key = (prev.id, curr.id)
                            if prev.id is not None:
                                bad_entries.add(prev.id)
                            if curr.id is not None:
                                bad_entries.add(curr.id)
                            bad_travel_pairs.add(pair_key)
                            issues.append(
                                f"{route.name}: travel block {prev.display_name} → {curr.display_name} is inconsistent"
                            )

                if esp is not None:
                    expected_empty_start = _t2m(prev.end_time) + travel_min + extra_min
                    expected_empty_end = expected_empty_start + empty_min
                    esp_start = _t2m(esp.start_time)
                    esp_end = _t2m(esp.end_time)
                    if esp_start != expected_empty_start or esp_end != expected_empty_end:
                        pair_key = (prev.id, curr.id)
                        if prev.id is not None:
                            bad_entries.add(prev.id)
                        if curr.id is not None:
                            bad_entries.add(curr.id)
                        bad_empty_pairs.add(pair_key)
                        issues.append(
                            f"{route.name}: gap block {prev.display_name} → {curr.display_name} is inconsistent"
                        )

            if bad_entries:
                inconsistent_by_route[route.id] = bad_entries
            if bad_travel_pairs:
                inconsistent_travel_by_route[route.id] = bad_travel_pairs
            if bad_empty_pairs:
                inconsistent_empty_by_route[route.id] = bad_empty_pairs

        return inconsistent_by_route, inconsistent_travel_by_route, inconsistent_empty_by_route, issues

    def _clear_debug_integrity_marks(self):
        self._route_scene.set_inconsistent_entries({})
        self._route_scene.set_inconsistent_blocks({}, {})

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(4, 4, 4, 4)
        root_layout.setSpacing(4)

        # Toolbar
        self._build_toolbar()

        # Filter bar – wraps to multiple rows as needed
        self._filter_bar = QWidget()
        self._filter_bar.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        filter_bar_vbox = QVBoxLayout(self._filter_bar)
        filter_bar_vbox.setContentsMargins(4, 2, 4, 2)
        filter_bar_vbox.setSpacing(2)

        filter_label_row = QHBoxLayout()
        self._filter_label = QLabel("Filter (Insatser):")
        filter_label_row.addWidget(self._filter_label)
        self._filter_mode_combo = QComboBox()
        self._filter_mode_combo.addItem("OR", "or")
        self._filter_mode_combo.addItem("AND", "and")
        self._filter_mode_combo.currentIndexChanged.connect(self._on_filter_changed)
        filter_label_row.addWidget(QLabel("Kombination:"))
        filter_label_row.addWidget(self._filter_mode_combo)
        filter_label_row.addStretch()
        filter_bar_vbox.addLayout(filter_label_row)

        self._filter_cb_container = QWidget()
        self._filter_cb_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._filter_layout = FlowLayout(self._filter_cb_container, h_spacing=6, v_spacing=4)
        self._filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_bar_vbox.addWidget(self._filter_cb_container)

        self._filter_buttons: dict[str, QPushButton] = {}
        root_layout.addWidget(self._filter_bar, 0)

        # Main splitter
        self._splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: routes
        self._route_scene = RouteScene(self._layout_engine)
        self._route_view = RouteView(self._route_scene)
        self._splitter.addWidget(self._route_view)

        # Right: pool
        pool_container = QWidget()
        pool_layout = QVBoxLayout(pool_container)
        pool_layout.setContentsMargins(0, 0, 0, 0)
        self._pool_scene = PoolScene(self._layout_engine)
        self._pool_view = PoolView(self._pool_scene)
        pool_layout.addWidget(self._pool_view)
        self._splitter.addWidget(pool_container)

        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 2)
        root_layout.addWidget(self._splitter, 1)  # stretch=1 so splitter takes all space

        # Sync scrollbars horizontally within each panel
        # (individual views already have their own scrollbars)

        # Wire scene signals
        self._wire_route_scene()
        self._wire_pool_scene()

    def _build_toolbar(self):
        tb = QToolBar("Huvudverktygsfält")
        tb.setMovable(False)
        self.addToolBar(tb)

        # Planfil
        act_import_state = QAction("Öppna planering", self)
        act_import_state.triggered.connect(self._on_import_state)
        tb.addAction(act_import_state)

        act_export_state = QAction("Spara planering", self)
        act_export_state.triggered.connect(self._on_export_state)
        tb.addAction(act_export_state)

        tb.addSeparator()

        # Inläsning (källdata)
        act_import = QAction("Importera Excel", self)
        act_import.triggered.connect(self._on_import_excel)
        tb.addAction(act_import)

        tb.addSeparator()

        # Export (resultat)
        act_export_excel = QAction("Exportera Excel", self)
        act_export_excel.triggered.connect(self._on_export_excel)
        tb.addAction(act_export_excel)

        act_export_pdf = QAction("Exportera PDF", self)
        act_export_pdf.triggered.connect(self._on_export_pdf)
        tb.addAction(act_export_pdf)

        tb.addSeparator()

        # Planering
        act_add_route = QAction("＋ Ny rutt", self)
        act_add_route.triggered.connect(self._on_add_route)
        tb.addAction(act_add_route)

        act_integrity = QAction("Tidsintegritet", self)
        act_integrity.triggered.connect(self._on_time_integrity)
        tb.addAction(act_integrity)

        tb.addSeparator()

        # Visning
        self._show_travel_action = QAction("Restid", self)
        self._show_travel_action.setCheckable(True)
        self._show_travel_action.setChecked(bool(self._settings.show_travel_blocks))
        self._show_travel_action.toggled.connect(self._on_block_visibility_changed)
        tb.addAction(self._show_travel_action)

        self._show_space_action = QAction("Lucka", self)
        self._show_space_action.setCheckable(True)
        self._show_space_action.setChecked(bool(self._settings.show_space_blocks))
        self._show_space_action.toggled.connect(self._on_block_visibility_changed)
        tb.addAction(self._show_space_action)

        self._show_extra_time_action = QAction("Extratid", self)
        self._show_extra_time_action.setCheckable(True)
        self._show_extra_time_action.setChecked(bool(self._settings.show_extra_time_blocks))
        self._show_extra_time_action.toggled.connect(self._on_block_visibility_changed)
        tb.addAction(self._show_extra_time_action)

        tb.addSeparator()

        # Tidsinställningar
        tb.addWidget(QLabel(" Färdsätt: "))
        self._mode_combo = QComboBox()
        for mode, label in [(TravelMode.CAR, "Bil"),
                             (TravelMode.BIKE, "Cykel"),
                             (TravelMode.WALK, "Gång")]:
            self._mode_combo.addItem(label, mode)
        idx = self._mode_combo.findData(self._settings.default_travel_mode)
        if idx >= 0:
            self._mode_combo.setCurrentIndex(idx)
        self._mode_combo.currentIndexChanged.connect(self._on_default_mode_changed)
        tb.addWidget(self._mode_combo)

        self._extra_time_auto_action = QAction("Auto extratid", self)
        self._extra_time_auto_action.setCheckable(True)
        self._extra_time_auto_action.setChecked(bool(self._settings.extra_time_auto_place))
        self._extra_time_auto_action.toggled.connect(self._on_extra_time_auto_toggled)
        tb.addAction(self._extra_time_auto_action)

        tb.addWidget(QLabel(" Extratid: "))
        self._extra_time_spin = QSpinBox()
        self._extra_time_spin.setRange(0, 120)
        self._extra_time_spin.setSuffix(" min")
        self._extra_time_spin.setValue(max(0, int(self._settings.extra_time_minutes)))
        self._extra_time_spin.valueChanged.connect(self._on_extra_time_minutes_changed)
        tb.addWidget(self._extra_time_spin)

        tb.addSeparator()

        # System
        act_travel_log = QAction("API-logg", self)
        act_travel_log.triggered.connect(self._show_travel_status)
        tb.addAction(act_travel_log)

        act_settings = QAction("Inställningar", self)
        act_settings.triggered.connect(self._on_open_settings)
        tb.addAction(act_settings)

        act_map = QAction("Karta", self)
        act_map.triggered.connect(self._on_open_map)
        tb.addAction(act_map)

        tb.addSeparator()

        act_reset_all = QAction("Rensa allt", self)
        act_reset_all.triggered.connect(self._on_reset_all)
        tb.addAction(act_reset_all)

    def _wire_route_scene(self):
        s = self._route_scene
        s.route_renamed.connect(self._on_route_renamed)
        s.route_notes_changed.connect(self._on_route_notes_changed)
        s.route_delete_requested.connect(self._on_route_delete)
        s.entry_dropped.connect(self._on_entry_dropped)
        s.entry_moved.connect(self._on_entry_moved)
        s.entry_time_edit.connect(self._on_entry_time_edit)
        s.entry_duration_changed.connect(self._on_entry_duration_changed)
        s.entry_color_changed.connect(self._on_entry_color_changed)
        s.entry_remove_requested.connect(self._on_entry_remove_requested)
        s.empty_remove.connect(self._on_empty_remove)
        s.extra_time_remove.connect(self._on_extra_time_remove)
        s.travel_mode_changed.connect(self._on_travel_mode_changed)
        s.travel_duration_changed.connect(self._on_travel_duration_changed)
        s.travel_minutes_edit.connect(self._on_travel_minutes_edit)
        s.travel_retry.connect(self._on_travel_retry)
        s.travel_source_toggle.connect(self._on_travel_source_toggle)
        s.visit_selected.connect(self._on_route_visit_selected)

    def _wire_pool_scene(self):
        self._pool_scene.entry_returned_to_pool.connect(self._on_entry_returned)
        self._pool_scene.visit_selected.connect(self._on_pool_visit_selected)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_data(self):
        visits = self._db.load_all_visits()
        self._visits = {v.id: v for v in visits}

        self._precache_map_addresses(visits)

        routes = self._db.load_all_routes(self._visits)
        self._routes = {r.id: r for r in routes}

        if self._settings.extra_time_auto_place:
            for route in routes:
                if route.extra_time_blocks:
                    continue
                entries = route.sorted_entries()
                for entry in entries[1:]:
                    route.extra_time_blocks.append(
                        ExtraTimeBlock(id=None, route_id=route.id, to_entry_id=entry.id)
                    )
                if entries:
                    self._recalc.recalculate(route)

        self._compute_pairs()
        self._build_filter_bar()

        # Pool: visits NOT placed in any route
        placed = self._db.get_placed_visit_ids()
        pool_visits = [v for v in visits if v.id not in placed]

        self._pool_scene.load(self._default_templates, pool_visits)
        self._route_scene.load_routes(routes)
        self._pool_scene.set_extra_time_minutes(self._settings.extra_time_minutes)
        self._route_scene.set_extra_time_minutes(self._settings.extra_time_minutes)
        self._on_block_visibility_changed()
        self._sync_map_window_visits()
        self._sync_map_selection()

    def _precache_map_addresses(self, visits: list[Visit]):
        if not self._api_key:
            return
        addresses: list[str] = []
        office_address = self._default_templates[0].get("address", "") if self._default_templates else ""
        if office_address:
            addresses.append(office_address)
        for visit in visits:
            address = (visit.full_address or visit.address or "").strip()
            if address:
                addresses.append(address)
        if not addresses:
            return
        geocode_svc = MapGeocodingService(self._db, self._api_key)
        geocode_svc.precache_addresses(addresses)

    @Slot()
    def _on_open_map(self):
        if self._map_window is None:
            office_address = self._default_templates[0].get("address", "") if self._default_templates else ""
            self._map_window = VisitMapWindow(self._db, self._api_key, office_address, self)
            self._map_window.set_color_palette(self._settings.visit_ribbon_color_map())
            self._map_window.destroyed.connect(lambda *_: setattr(self, "_map_window", None))
            self._sync_map_window_visits()
            self._sync_map_selection()
        self._map_window.show()
        self._map_window.raise_()

    def _sync_map_window_visits(self):
        if self._map_window is None:
            return
        office_address = self._default_templates[0].get("address", "") if self._default_templates else ""
        self._map_window.set_office_address(office_address)
        self._map_window.set_color_palette(self._settings.visit_ribbon_color_map())
        self._map_window.set_visits(list(self._visits.values()))

    def _sync_map_selection(self):
        if self._map_window is None:
            return
        self._map_window.set_selected_visit_id(self._selected_visit_id)

    def _compute_pairs(self):
        """Find DUBBELBEMANNING 1 / DUBBELBEMANNING 2 pairs."""
        self._pairs.clear()
        cands: dict[str, list[Visit]] = {}
        for v in self._visits.values():
            ins = v.insatser.upper()
            if "DUBBELBEMANNING 1" in ins or "DUBBELBEMANNING 2" in ins:
                key = f"{v.name.strip().lower()}|{v.address.strip().lower()}"
                cands.setdefault(key, []).append(v)

        for group in cands.values():
            if len(group) < 2:
                continue
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    a, b = group[i], group[j]
                    a_ins = (a.insatser or "").upper()
                    b_ins = (b.insatser or "").upper()
                    a_is_1 = "DUBBELBEMANNING 1" in a_ins
                    a_is_2 = "DUBBELBEMANNING 2" in a_ins
                    b_is_1 = "DUBBELBEMANNING 1" in b_ins
                    b_is_2 = "DUBBELBEMANNING 2" in b_ins
                    valid_roles = (a_is_1 and b_is_2) or (a_is_2 and b_is_1)
                    if valid_roles and abs(_t2m(a.default_start) - _t2m(b.default_start)) <= 5:
                        self._pairs[a.id] = b.id
                        self._pairs[b.id] = a.id

    def _build_filter_bar(self):
        # Clear existing buttons (hide first to avoid flash)
        for btn in self._filter_buttons.values():
            btn.hide()
            btn.deleteLater()
        # Purge dead layout items
        self._filter_layout._items.clear()
        self._filter_buttons.clear()

        # Collect unique Insatser tokens
        tags: set[str] = set()
        for v in self._visits.values():
            for tag in v.insatser.split(","):
                t = tag.strip()
                if t:
                    tags.add(t)

        for tag in sorted(tags):
            btn = QPushButton(tag, self._filter_cb_container)
            btn.setCheckable(True)
            btn.setChecked(False)
            btn.toggled.connect(self._on_filter_changed)
            self._filter_buttons[tag] = btn
            self._filter_layout.addWidget(btn)

        self._filter_layout.invalidate()
        self._filter_cb_container.updateGeometry()

    # ------------------------------------------------------------------
    # Filter
    # ------------------------------------------------------------------

    @Slot()
    def _on_filter_changed(self):
        active = {tag for tag, btn in self._filter_buttons.items() if btn.isChecked()}
        mode = self._filter_mode_combo.currentData() or "or"
        self._route_scene.apply_filter(active, mode)
        self._pool_scene.apply_filter(active, mode)

    # ------------------------------------------------------------------
    # Route management
    # ------------------------------------------------------------------

    @Slot()
    def _on_add_route(self):
        name = self._next_default_route_name()
        order = max((r.display_order for r in self._routes.values()), default=-1) + 1
        route = self._db.create_route(name, order)
        self._routes[route.id] = route
        self._route_scene.add_route(route)

    def _next_default_route_name(self) -> str:
        used = {r.name for r in self._routes.values()}
        n = 1
        while True:
            candidate = f"Rutt {n}"
            if candidate not in used:
                return candidate
            n += 1

    @Slot(int, str)
    def _on_route_renamed(self, route_id: int, name: str):
        route = self._routes.get(route_id)
        if route:
            route.name = name
            self._db.save_route(route)

    @Slot(int, str)
    def _on_route_notes_changed(self, route_id: int, notes: str):
        route = self._routes.get(route_id)
        if route:
            route.notes = notes
            self._db.save_route(route)

    @Slot(int)
    def _on_route_delete(self, route_id: int):
        route = self._routes.get(route_id)
        if not route:
            return
        reply = QMessageBox.question(
            self, "Ta bort rutt",
            f"Ta bort rutten '{route.name}'? Besöken återförs till listan.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Return all visits to pool
        for entry in route.entries:
            if entry.visit_id and entry.visit_id in self._visits:
                v = self._visits[entry.visit_id]
                self._pool_scene.add_visit(v)

        self._db.delete_route(route_id)
        del self._routes[route_id]
        self._route_scene.remove_route(route_id)

    # ------------------------------------------------------------------
    # Entry drag-and-drop
    # ------------------------------------------------------------------

    @Slot(int, object)
    def _on_entry_dropped(self, route_id: int, data: dict):
        route = self._routes.get(route_id)
        if not route:
            return

        dtype = data.get("type")
        insert_index = data.get("insert_index")

        if dtype == "pool_visit":
            visit_id = data["visit_id"]
            visit = self._visits.get(visit_id)
            if not visit:
                return
            dur = max(1, _t2m(visit.default_end) - _t2m(visit.default_start))
            entry = RouteEntry(
                id=None, route_id=route_id, visit_id=visit_id,
                position=0,
                start_time="07:00",
                end_time=_m2t(_t2m("07:00") + dur),
                visit=visit,
            )
            self._recalc.add_entry_to_route(
                route, entry,
                self._settings.default_travel_mode,
                insert_index=insert_index,
            )
            if self._settings.extra_time_auto_place:
                self._ensure_extra_time_for_entry(route, entry.id)
            self._pool_scene.remove_visit(visit_id)
            self._request_travel_for_new_entry(route, entry)
            self._route_scene.rebuild_route(route)
            if insert_index is not None:
                self._route_scene.pop_visit(route_id, visit_index=int(insert_index))

        elif dtype == "office":
            duration = max(1, int(data.get("duration_minutes", 10)))
            entry = RouteEntry(
                id=None, route_id=route_id, visit_id=None,
                position=0,
                start_time="07:00", end_time=_m2t(_t2m("07:00") + duration),
                is_office_instance=True,
                office_name=data.get("name", "Kontor"),
                office_address=data.get("address", ""),
            )
            self._recalc.add_entry_to_route(
                route, entry,
                self._settings.default_travel_mode,
                insert_index=insert_index,
            )
            self._route_scene.rebuild_route(route)
            if insert_index is not None:
                self._route_scene.pop_visit(route_id, visit_index=int(insert_index))

        elif dtype == "extra_time":
            if insert_index is None:
                insert_index = len(route.sorted_entries())
            entries = route.sorted_entries()
            if insert_index <= 0 or insert_index >= len(entries):
                return
            target = entries[insert_index]
            self._ensure_extra_time_for_entry(route, target.id)
            self._recalc.recalculate(route)
            self._route_scene.rebuild_route(route)
            self._autosave.mark_dirty(route.id)
            return

        elif dtype == "route_entry":
            # Move entry from one route to another (or reorder within same)
            entry_id = data["entry_id"]
            src_route_id = data["source_route_id"]
            if src_route_id == route_id:
                entries = route.sorted_entries()
                current_idx = next((i for i, e in enumerate(entries) if e.id == entry_id), None)
                if current_idx is None or insert_index is None:
                    return
                # insert_index is computed in the pre-removal coordinate space [0..len(entries)]
                insert_index = max(0, min(insert_index, len(entries)))

                # When moving forward, removing the source shifts later indices left by one.
                adjusted_index = insert_index - 1 if insert_index > current_idx else insert_index
                adjusted_index = max(0, min(adjusted_index, len(entries) - 1))

                if adjusted_index == current_idx:
                    return
                entry = entries.pop(current_idx)
                # After pop, list length is len(entries)-1; insert accepts index up to new len.
                adjusted_index = max(0, min(adjusted_index, len(entries)))
                entries.insert(adjusted_index, entry)
                for i, e in enumerate(entries):
                    e.position = i
                route.entries = entries
                self._recalc.recalculate(route)
                self._route_scene.rebuild_route(route)
                self._route_scene.pop_visit(route_id, visit_index=adjusted_index)
                self._autosave.mark_dirty(route.id)
                return
            src_route = self._routes.get(src_route_id)
            if not src_route:
                return
            entry = next((e for e in src_route.entries if e.id == entry_id), None)
            if not entry:
                return
            # Remove from source
            self._recalc.remove_entry_from_route(src_route, entry, replace_with_empty=False)
            # Reset position and add to target
            entry.route_id = route_id
            entry.id = None
            self._recalc.add_entry_to_route(
                route, entry,
                self._settings.default_travel_mode,
                insert_index=insert_index,
            )
            if self._settings.extra_time_auto_place and entry.visit_id and not entry.is_office_instance:
                self._ensure_extra_time_for_entry(route, entry.id)
            self._route_scene.rebuild_route(src_route)
            self._route_scene.rebuild_route(route)
            if insert_index is not None:
                self._route_scene.pop_visit(route_id, visit_index=int(insert_index))

        self._autosave.mark_dirty(route.id)

    @Slot(int)
    def _on_entry_returned(self, entry_id: int):
        """Visit dragged back to pool."""
        for route in self._routes.values():
            entry = next((e for e in route.entries if e.id == entry_id), None)
            if entry:
                if entry.visit_id and entry.visit_id in self._visits:
                    self._remove_entry_to_pool(route, entry)
                return

    def _remove_entry_to_pool(self, route: Route, entry: RouteEntry):
        if not entry.visit_id or entry.visit_id not in self._visits:
            return
        v = self._visits[entry.visit_id]
        v_restored = Visit(
            id=v.id, object_id=v.object_id, name=v.name,
            address=v.address, street=v.street,
            default_start=v.default_start, default_end=v.default_end,
            insatser=v.insatser, color=v.color, raw_data=v.raw_data,
            full_address=v.full_address,
        )
        self._recalc.remove_entry_from_route(route, entry, replace_with_empty=True)
        self._pool_scene.add_visit(v_restored)
        self._route_scene.rebuild_route(route)
        self._autosave.mark_dirty(route.id)

    def _ensure_extra_time_for_entry(self, route: Route, to_entry_id: Optional[int]):
        if not to_entry_id:
            return
        entries = route.sorted_entries()
        idx = next((i for i, e in enumerate(entries) if e.id == to_entry_id), None)
        if idx is None or idx == 0:
            return
        existing = route.extra_time_for_entry(to_entry_id)
        if existing:
            return
        route.extra_time_blocks.append(
            ExtraTimeBlock(id=None, route_id=route.id, to_entry_id=to_entry_id)
        )

    def _remove_extra_time_for_entry(self, route: Route, to_entry_id: int):
        block = route.extra_time_for_entry(to_entry_id)
        if not block:
            return
        route.extra_time_blocks = [
            b for b in route.extra_time_blocks if b.to_entry_id != to_entry_id
        ]

    @Slot(int, int)
    def _on_extra_time_remove(self, route_id: int, to_entry_id: int):
        route = self._routes.get(route_id)
        if not route:
            return
        self._remove_extra_time_for_entry(route, to_entry_id)
        self._recalc.recalculate(route)
        self._route_scene.rebuild_route(route)
        self._autosave.mark_dirty(route.id)

    @Slot(int, int, object)
    def _on_entry_color_changed(self, route_id: int, entry_id: int, color):
        route = self._routes.get(route_id)
        if not route:
            return
        entry = next((e for e in route.entries if e.id == entry_id), None)
        if not entry:
            return
        if entry.is_office_instance:
            entry.office_color = color or "black"
            self._db.update_route_entry(entry)
        else:
            if not entry.visit:
                return
            entry.visit.color = color
            self._db.upsert_visit(entry.visit)
        self._route_scene.rebuild_route(route)
        self._sync_map_window_visits()

    @Slot(int, int)
    def _on_entry_remove_requested(self, route_id: int, entry_id: int):
        route = self._routes.get(route_id)
        if not route:
            return
        entry = next((e for e in route.entries if e.id == entry_id), None)
        if not entry:
            return
        if entry.visit_id and entry.visit_id in self._visits:
            self._remove_entry_to_pool(route, entry)
        elif entry.is_office_instance:
            self._recalc.remove_entry_from_route(route, entry, replace_with_empty=False)
            self._route_scene.rebuild_route(route)
            self._autosave.mark_dirty(route.id)

    # ------------------------------------------------------------------
    # Entry reordering
    # ------------------------------------------------------------------

    @Slot(int, int, int)
    def _on_entry_moved(self, route_id: int, entry_id: int, direction: int):
        route = self._routes.get(route_id)
        if not route:
            return
        entries = route.sorted_entries()
        idx = next((i for i, e in enumerate(entries) if e.id == entry_id), None)
        if idx is None:
            return
        target = idx + direction
        if target < 0 or target >= len(entries):
            return
        self._recalc.swap_entries(route, entries[idx], entries[target])
        self._route_scene.rebuild_route(route)
        self._autosave.mark_dirty(route_id)

    # ------------------------------------------------------------------
    # Time editing
    # ------------------------------------------------------------------

    @Slot(int, int)
    def _on_entry_time_edit(self, route_id: int, entry_id: int):
        route = self._routes.get(route_id)
        if not route:
            return
        entry = next((e for e in route.entries if e.id == entry_id), None)
        if not entry:
            return

        entries = route.sorted_entries()
        entry_idx = next((i for i, e in enumerate(entries) if e.id == entry_id), 0)

        # Calculate minimum allowed start (must keep one-minute integrity)
        min_start = 0
        if entry_idx > 0:
            prev = entries[entry_idx - 1]
            seg = route.travel_segment_between(prev.id, entry.id)
            travel = seg.travel_minutes if seg else 0
            extra = self._settings.extra_time_minutes if route.extra_time_for_entry(entry.id) else 0
            min_start = _t2m(prev.end_time) + travel + extra

        current_start_abs = _t2m(entry.start_time)
        current_duration = max(1, _t2m(entry.end_time) - _t2m(entry.start_time))

        dlg = QDialog(self)
        dlg.setWindowTitle("Redigera besök")
        form = QFormLayout(dlg)

        start_edit = QLineEdit(_display_time(entry.start_time), dlg)
        start_edit.setPlaceholderText("HH:MM")
        duration_spin = QSpinBox(dlg)
        duration_spin.setRange(1, 720)
        duration_spin.setValue(current_duration)
        duration_spin.setSuffix(" min")

        form.addRow("Starttid (HH:MM):", start_edit)
        form.addRow("Duration:", duration_spin)
        if entry_idx > 0:
            form.addRow("Tidigast:", QLabel(_display_time(_m2t(min_start)), dlg))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, parent=dlg)
        form.addRow(buttons)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        import re
        text = start_edit.text().strip()
        m = re.match(r"^(\d{1,2}):(\d{2})$", text)
        if not m:
            QMessageBox.warning(self, "Fel format", "Ange tid som HH:MM")
            return

        h = int(m.group(1))
        mm = int(m.group(2))
        if h < 0 or h > 23 or mm < 0 or mm > 59:
            QMessageBox.warning(self, "Fel format", "Ange giltig tid (00:00–23:59)")
            return

        target_day = current_start_abs // 1440
        new_start = target_day * 1440 + h * 60 + mm
        if current_start_abs - new_start > 720:
            new_start += 1440
        elif new_start - current_start_abs > 720:
            new_start -= 1440

        if new_start < min_start:
            QMessageBox.warning(
                self, "Ogiltig tid",
                f"Starttiden kräver tillräcklig lucka före besöket. Tidigast {_display_time(_m2t(min_start))}."
            )
            return

        duration = duration_spin.value()
        old_start = _t2m(entry.start_time)
        old_end = _t2m(entry.end_time)
        entry.start_time = _m2t(new_start)
        entry.end_time = _m2t(new_start + duration)

        if entry_idx > 0:
            prev = entries[entry_idx - 1]
            seg = route.travel_segment_between(prev.id, entry.id)
            travel = seg.travel_minutes if seg else 0
            extra = self._settings.extra_time_minutes if route.extra_time_for_entry(entry.id) else 0
            base_start = _t2m(prev.end_time) + travel + extra
            manual_gap = max(0, new_start - base_start)
            space = route.empty_space_between(prev.id, entry.id)
            if manual_gap > 0:
                if space is None:
                    route.empty_spaces.append(EmptySpace(
                        id=None,
                        route_id=route.id,
                        from_entry_id=prev.id,
                        to_entry_id=entry.id,
                        duration_minutes=manual_gap,
                    ))
                else:
                    space.duration_minutes = manual_gap
            elif space is not None:
                route.empty_spaces.remove(space)

        new_end = _t2m(entry.end_time)
        self._recalc.shift_following_entries(route, entry.id, new_end - old_end,
                             include_anchor=False)

        self._recalc.recalculate(route)
        self._route_scene.rebuild_route(route)
        self._autosave.mark_dirty(route.id)

    @Slot(int, int, int)
    def _on_entry_duration_changed(self, route_id: int, entry_id: int, delta: int):
        route = self._routes.get(route_id)
        if not route:
            return
        entry = next((e for e in route.entries if e.id == entry_id), None)
        if not entry:
            return
        current = _t2m(entry.end_time) - _t2m(entry.start_time)
        new_dur = max(1, current + delta)
        effective_delta = new_dur - current

        shift_delta = effective_delta
        if effective_delta > 0:
            entries = route.sorted_entries()
            idx = next((i for i, e in enumerate(entries) if e.id == entry.id), None)
            if idx is not None and idx < len(entries) - 1:
                nxt = entries[idx + 1]
                space = route.empty_space_between(entry.id, nxt.id)
                if space and space.duration_minutes > 0:
                    consume = min(effective_delta, space.duration_minutes)
                    space.duration_minutes -= consume
                    if space.duration_minutes <= 0:
                        route.empty_spaces.remove(space)
                    shift_delta = effective_delta - consume

        entry.end_time = _m2t(_t2m(entry.start_time) + new_dur)
        self._recalc.shift_following_entries(route, entry.id, shift_delta)
        self._recalc.recalculate(route)
        self._route_scene.rebuild_route(route)
        self._autosave.mark_dirty(route.id)

    @Slot(int, int, int)
    def _on_empty_remove(self, route_id: int, from_entry_id: int, to_entry_id: int):
        route = self._routes.get(route_id)
        if not route:
            return
        space = route.empty_space_between(from_entry_id, to_entry_id)
        if not space:
            return
        removed = max(0, space.duration_minutes)
        route.empty_spaces.remove(space)
        to_entry = next((e for e in route.entries if e.id == to_entry_id), None)
        if to_entry and removed > 0:
            self._recalc.shift_following_entries(route, to_entry.id, -removed,
                                                 include_anchor=True)
        self._recalc.recalculate(route)
        self._route_scene.rebuild_route(route)
        self._autosave.mark_dirty(route.id)

    # ------------------------------------------------------------------
    # Travel segment editing
    # ------------------------------------------------------------------

    @Slot(int, int, str)
    def _on_travel_mode_changed(self, route_id: int, seg_id: int, mode: str):
        route = self._routes.get(route_id)
        if not route:
            return
        seg = next((s for s in route.travel_segments if s.id == seg_id), None)
        if not seg:
            return
        seg.mode = mode
        seg.api_failed = False
        seg.api_error = ""
        seg.is_calculating = False
        seg.travel_time_state = TravelTimeState.DEFAULT
        self._recalc.recalculate(route)
        self._route_scene.rebuild_route(route)
        self._autosave.mark_dirty(route_id)

        self._request_segment_travel(route, seg, debounce_ms=700)

    @Slot(int, int, int)
    def _on_travel_duration_changed(self, route_id: int, seg_id: int, delta: int):
        route = self._routes.get(route_id)
        if not route:
            return
        seg = next((s for s in route.travel_segments if s.id == seg_id), None)
        if not seg:
            return
        old_minutes = max(0, seg.travel_minutes)
        new_minutes = max(0, old_minutes + delta)
        change = new_minutes - old_minutes

        if not seg.is_custom:
            seg.calculated_minutes = old_minutes
        seg.travel_minutes = new_minutes
        seg.is_custom = True
        seg.travel_time_state = TravelTimeState.EDITED
        seg.api_failed = False
        seg.api_error = ""
        seg.is_calculating = False

        shift_delta = change
        if change > 0:
            space = route.empty_space_between(seg.from_entry_id, seg.to_entry_id)
            if space and space.duration_minutes > 0:
                consume = min(change, space.duration_minutes)
                space.duration_minutes -= consume
                if space.duration_minutes <= 0:
                    route.empty_spaces.remove(space)
                shift_delta = change - consume

        if shift_delta != 0:
            to_entry = next((e for e in route.entries if e.id == seg.to_entry_id), None)
            if to_entry:
                self._recalc.shift_following_entries(route, to_entry.id, shift_delta,
                                                     include_anchor=True)

        self._recalc.recalculate(route)
        self._route_scene.rebuild_route(route)
        self._autosave.mark_dirty(route_id)

    @Slot(int, int)
    def _on_travel_minutes_edit(self, route_id: int, seg_id: int):
        route = self._routes.get(route_id)
        if not route:
            return
        seg = next((s for s in route.travel_segments if s.id == seg_id), None)
        if not seg:
            return
        val, ok = QInputDialog.getInt(
            self, "Redigera resetid",
            "Resetid (minuter):",
            value=seg.travel_minutes, minValue=0, maxValue=300,
        )
        if ok:
            old_minutes = max(0, seg.travel_minutes)
            new_minutes = max(0, val)
            change = new_minutes - old_minutes

            if not seg.is_custom:
                seg.calculated_minutes = old_minutes
            seg.travel_minutes = new_minutes
            seg.is_custom = True
            seg.travel_time_state = TravelTimeState.EDITED
            seg.api_failed = False
            seg.api_error = ""
            seg.is_calculating = False

            shift_delta = change
            if change > 0:
                space = route.empty_space_between(seg.from_entry_id, seg.to_entry_id)
                if space and space.duration_minutes > 0:
                    consume = min(change, space.duration_minutes)
                    space.duration_minutes -= consume
                    if space.duration_minutes <= 0:
                        route.empty_spaces.remove(space)
                    shift_delta = change - consume

            if shift_delta != 0:
                to_entry = next((e for e in route.entries if e.id == seg.to_entry_id), None)
                if to_entry:
                    self._recalc.shift_following_entries(route, to_entry.id, shift_delta,
                                                         include_anchor=True)

            self._recalc.recalculate(route)
            self._route_scene.rebuild_route(route)
            self._autosave.mark_dirty(route_id)

    @Slot(int, int)
    def _on_travel_retry(self, route_id: int, seg_id: int):
        route = self._routes.get(route_id)
        if not route:
            return
        seg = next((s for s in route.travel_segments if s.id == seg_id), None)
        if not seg:
            return
        self._request_segment_travel(route, seg, debounce_ms=0)

    @Slot(int, int)
    def _on_travel_source_toggle(self, route_id: int, seg_id: int):
        route = self._routes.get(route_id)
        if not route:
            return
        seg = next((s for s in route.travel_segments if s.id == seg_id), None)
        if not seg:
            return

        if seg.travel_time_state == TravelTimeState.EDITED:
            self._apply_segment_minutes(route, seg,
                                        self._settings.default_travel_for_mode(seg.mode),
                                        is_custom=False,
                                        state=TravelTimeState.DEFAULT)
            return

        if seg.travel_time_state == TravelTimeState.CALCULATED and not seg.api_failed:
            self._apply_segment_minutes(route, seg,
                                        self._settings.default_travel_for_mode(seg.mode),
                                        is_custom=False,
                                        state=TravelTimeState.DEFAULT)
            return

        self._request_segment_travel(route, seg, debounce_ms=0)

    def _apply_segment_minutes(self, route: Route, seg: TravelSegment, minutes: int,
                               is_custom: bool, state: str):
        old_minutes = max(0, seg.travel_minutes)
        new_minutes = max(0, int(minutes))
        change = new_minutes - old_minutes

        seg.travel_minutes = new_minutes
        seg.is_custom = is_custom
        seg.travel_time_state = state
        seg.api_failed = False
        seg.api_error = ""
        seg.is_calculating = False

        shift_delta = change
        if change > 0:
            space = route.empty_space_between(seg.from_entry_id, seg.to_entry_id)
            if space and space.duration_minutes > 0:
                consume = min(change, space.duration_minutes)
                space.duration_minutes -= consume
                if space.duration_minutes <= 0:
                    route.empty_spaces.remove(space)
                shift_delta = change - consume

        if shift_delta != 0:
            to_entry = next((e for e in route.entries if e.id == seg.to_entry_id), None)
            if to_entry:
                self._recalc.shift_following_entries(route, to_entry.id, shift_delta,
                                                     include_anchor=True)

        self._recalc.recalculate(route)
        self._route_scene.rebuild_route(route)
        self._autosave.mark_dirty(route.id)

    # ------------------------------------------------------------------
    # Travel time async result
    # ------------------------------------------------------------------

    @Slot(str, str, str, int)
    def _on_travel_time_ready(self, from_addr: str, to_addr: str,
                               mode: str, minutes: int):
        """Update any route segments that use this pair."""
        key = (from_addr, to_addr, mode)
        from_failed_fallback = key in self._failed_fallback_keys
        if from_failed_fallback:
            self._failed_fallback_keys.discard(key)

        for route in self._routes.values():
            changed = False
            for seg in route.travel_segments:
                if seg.is_custom:
                    continue
                if not seg.is_calculating:
                    continue
                from_entry = next(
                    (e for e in route.entries if e.id == seg.from_entry_id), None)
                to_entry = next(
                    (e for e in route.entries if e.id == seg.to_entry_id), None)
                if not from_entry or not to_entry:
                    continue
                if (from_entry.api_address == from_addr and
                        to_entry.api_address == to_addr and
                        seg.mode == mode):
                    seg.travel_minutes = minutes
                    seg.calculated_minutes = minutes
                    seg.is_calculating = False
                    if not from_failed_fallback:
                        seg.api_failed = False
                        seg.api_error = ""
                        seg.travel_time_state = TravelTimeState.CALCULATED
                    changed = True
            if changed:
                self._recalc.recalculate(route)
                self._route_scene.rebuild_route(route)
                self._autosave.mark_dirty(route.id)
        if self._settings.debug_mode:
            self._run_debug_integrity_scan("travel_time_ready", log_result=False)

    def _connect_travel_signals(self):
        self._travel_svc.travel_time_ready.connect(self._on_travel_time_ready)
        self._travel_svc.lookup_started.connect(self._on_travel_lookup_started)
        self._travel_svc.request_logged.connect(
            lambda t: self._get_travel_status().log_request(t))
        self._travel_svc.response_logged.connect(
            lambda t: self._get_travel_status().log_response(t))
        self._travel_svc.error_occurred.connect(self._on_travel_time_error)
        self._travel_svc.quota_warning.connect(
            lambda c: (self._get_travel_status().log_quota_warning(c),
                       QMessageBox.warning(
                           self, "API-kvot",
                           f"Du har nu gjort {c} API-anrop. "
                           "Kontrollera ditt kvotgräns i Inställningar."
                       )))

    def _request_travel_for_new_entry(self, route: Route, new_entry: RouteEntry):
        """Fire async travel time requests for neighbors of a newly placed entry."""
        entries = route.sorted_entries()
        idx = next((i for i, e in enumerate(entries) if e.id == new_entry.id), None)
        if idx is None:
            return
        if idx > 0:
            prev = entries[idx - 1]
            seg_prev = route.travel_segment_between(prev.id, new_entry.id)
            if seg_prev:
                self._request_segment_travel(route, seg_prev, debounce_ms=0)
        if idx < len(entries) - 1:
            nxt = entries[idx + 1]
            seg_next = route.travel_segment_between(new_entry.id, nxt.id)
            if seg_next:
                self._request_segment_travel(route, seg_next, debounce_ms=0)

    @Slot(str, str, str)
    def _on_travel_lookup_started(self, from_addr: str, to_addr: str, mode: str):
        self._set_segments_lookup_state(from_addr, to_addr, mode, is_calculating=True,
                                        api_failed=False, api_error="")
        if self._settings.debug_mode:
            self._run_debug_integrity_scan("travel_lookup_started", log_result=False)

    @Slot(str, str, str, str)
    def _on_travel_time_error(self, from_addr: str, to_addr: str, mode: str, error_msg: str):
        self._get_travel_status().log_error(from_addr, to_addr, mode, error_msg)
        self._failed_fallback_keys.add((from_addr, to_addr, mode))
        self._set_segments_lookup_state(from_addr, to_addr, mode, is_calculating=False,
                                        api_failed=True, api_error=error_msg)
        if self._settings.debug_mode:
            self._run_debug_integrity_scan("travel_time_error", log_result=False)

    def _set_segments_lookup_state(self, from_addr: str, to_addr: str, mode: str,
                                   is_calculating: bool, api_failed: bool,
                                   api_error: str = ""):
        for route in self._routes.values():
            changed = False
            for seg in route.travel_segments:
                from_entry = next((e for e in route.entries if e.id == seg.from_entry_id), None)
                to_entry = next((e for e in route.entries if e.id == seg.to_entry_id), None)
                if not from_entry or not to_entry:
                    continue
                if (from_entry.api_address == from_addr and
                        to_entry.api_address == to_addr and
                        seg.mode == mode):
                    seg.is_calculating = is_calculating
                    seg.api_failed = api_failed
                    seg.api_error = api_error
                    if api_failed:
                        seg.travel_time_state = TravelTimeState.DEFAULT
                    changed = True
            if changed:
                self._route_scene.rebuild_route(route)

    def _request_segment_travel(self, route: Route, seg: TravelSegment,
                                debounce_ms: int = 0):
        from_entry = next((e for e in route.entries if e.id == seg.from_entry_id), None)
        to_entry = next((e for e in route.entries if e.id == seg.to_entry_id), None)
        if not from_entry or not to_entry:
            return

        from_addr = from_entry.api_address
        to_addr = to_entry.api_address
        if not from_addr or not to_addr:
            return

        seg.is_calculating = True
        seg.api_failed = False
        seg.api_error = ""
        self._route_scene.rebuild_route(route)

        timer_key = (route.id, seg.from_entry_id, seg.to_entry_id)
        existing = self._travel_debounce_timers.pop(timer_key, None)
        if existing is not None:
            existing.stop()
            existing.deleteLater()

        def _fire_request():
            self._travel_debounce_timers.pop(timer_key, None)
            self._travel_svc.request_travel_time(from_addr, to_addr, seg.mode)

        if debounce_ms > 0:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(_fire_request)
            self._travel_debounce_timers[timer_key] = timer
            timer.start(int(debounce_ms))
        else:
            _fire_request()

    @Slot()
    def _on_time_integrity(self):
        before = self._capture_integrity_snapshot()
        for route in self._routes.values():
            self._recalc.recalculate(route)
            self._route_scene.rebuild_route(route)
        after = self._capture_integrity_snapshot()
        self._log_integrity_changes(before, after)
        self._autosave.flush_now()

    def _capture_integrity_snapshot(self) -> dict:
        snapshot: dict = {
            "entries": {},
            "segments": {},
            "spaces": {},
        }
        for route in self._routes.values():
            for entry in route.entries:
                if entry.id is None:
                    continue
                snapshot["entries"][entry.id] = (
                    route.id,
                    route.name,
                    entry.display_name,
                    entry.start_time,
                    entry.end_time,
                )
            for seg in route.travel_segments:
                if seg.id is None:
                    continue
                snapshot["segments"][seg.id] = (
                    route.id,
                    route.name,
                    seg.from_entry_id,
                    seg.to_entry_id,
                    seg.mode,
                    seg.travel_minutes,
                    seg.start_time,
                    seg.end_time,
                )
            for esp in route.empty_spaces:
                if esp.id is None:
                    continue
                snapshot["spaces"][esp.id] = (
                    route.id,
                    route.name,
                    esp.from_entry_id,
                    esp.to_entry_id,
                    esp.duration_minutes,
                    esp.start_time,
                    esp.end_time,
                )
        return snapshot

    def _log_integrity_changes(self, before: dict, after: dict):
        lines: list[str] = []

        for entry_id, after_val in after["entries"].items():
            before_val = before["entries"].get(entry_id)
            if before_val and before_val[3:] != after_val[3:]:
                route_name = after_val[1]
                visit_name = after_val[2]
                lines.append(
                    f"{route_name} | Visit '{visit_name}': "
                    f"{_display_time(before_val[3])}–{_display_time(before_val[4])} → "
                    f"{_display_time(after_val[3])}–{_display_time(after_val[4])}"
                )

        for seg_id, after_val in after["segments"].items():
            before_val = before["segments"].get(seg_id)
            if before_val and before_val[5:] != after_val[5:]:
                route_name = after_val[1]
                lines.append(
                    f"{route_name} | Travel {after_val[2]}→{after_val[3]}: "
                    f"{before_val[5]} min ({_display_time(before_val[6])}–{_display_time(before_val[7])}) → "
                    f"{after_val[5]} min ({_display_time(after_val[6])}–{_display_time(after_val[7])})"
                )

        for space_id, after_val in after["spaces"].items():
            before_val = before["spaces"].get(space_id)
            if before_val and before_val[4:] != after_val[4:]:
                route_name = after_val[1]
                lines.append(
                    f"{route_name} | Gap {after_val[2]}→{after_val[3]}: "
                    f"{before_val[4]} min ({_display_time(before_val[5])}–{_display_time(before_val[6])}) → "
                    f"{after_val[4]} min ({_display_time(after_val[5])}–{_display_time(after_val[6])})"
                )

        if lines:
            self._get_travel_status().log_integrity_change(
                f"Time integrity made {len(lines)} changes"
            )
            for line in lines[:120]:
                self._get_travel_status().log_integrity_change(line)
            if len(lines) > 120:
                self._get_travel_status().log_integrity_change(
                    f"... and {len(lines) - 120} more"
                )
        else:
            self._get_travel_status().log_integrity_ok("Time integrity: no changes needed")

    @Slot()
    def _on_block_visibility_changed(self):
        self._settings.show_travel_blocks = self._show_travel_action.isChecked()
        self._settings.show_space_blocks = self._show_space_action.isChecked()
        self._settings.show_extra_time_blocks = self._show_extra_time_action.isChecked()
        self._route_scene.set_block_visibility(
            self._settings.show_travel_blocks,
            self._settings.show_space_blocks,
            self._settings.show_extra_time_blocks,
        )
        self._autosave.mark_dirty()

    @Slot(bool)
    def _on_extra_time_auto_toggled(self, checked: bool):
        self._settings.extra_time_auto_place = bool(checked)
        self._autosave.mark_dirty()

    @Slot(int)
    def _on_extra_time_minutes_changed(self, minutes: int):
        self._settings.extra_time_minutes = max(0, int(minutes))
        if self._default_templates and self._default_templates[-1].get("type") == "extra_time":
            self._default_templates[-1]["duration_minutes"] = self._settings.extra_time_minutes
        self._pool_scene.set_extra_time_minutes(self._settings.extra_time_minutes)
        self._route_scene.set_extra_time_minutes(self._settings.extra_time_minutes)
        for route in self._routes.values():
            self._recalc.recalculate(route)
            self._route_scene.rebuild_route(route)
        self._autosave.mark_dirty()

    @Slot()
    def _on_reset_all(self):
        reply = QMessageBox.question(
            self, "Rensa allt",
            "Detta tar bort alla rutter och besök från planeringen. Fortsätta?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        current_state = self._db.export_state()
        self._db.import_state({
            "visits": [],
            "routes": [],
            "route_visit_order": [],
            "travel_segments": [],
            "empty_spaces": [],
            "extra_time_blocks": [],
            "settings": current_state.get("settings", []),
            "column_order": current_state.get("column_order", []),
            "office_template": current_state.get("office_template", []),
        })
        self._load_data()
        self._autosave.clear()
        QMessageBox.information(self, "Rensat", "All planering har rensats.")

    # ------------------------------------------------------------------
    # Default travel mode
    # ------------------------------------------------------------------

    @Slot(int)
    def _on_default_mode_changed(self, idx: int):
        self._settings.default_travel_mode = self._mode_combo.currentData()
        self._autosave.mark_dirty()

    # ------------------------------------------------------------------
    # Font size
    # ------------------------------------------------------------------

    @Slot(int)
    def _on_font_size_changed(self, size: int):
        self._settings.font_size = size
        self._layout_engine.font_size = size
        self._route_scene.set_font_size(size)
        self._pool_scene.set_font_size(size)
        self._autosave.mark_dirty()

    # ------------------------------------------------------------------
    # Excel import
    # ------------------------------------------------------------------

    @Slot()
    def _on_import_excel(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Öppna Excel-fil", "", "Excel-filer (*.xls *.xlsx)"
        )
        if not path:
            return

        try:
            visits, result = self._import_svc.preview_import(path)
        except Exception as exc:
            QMessageBox.critical(self, "Importfel", str(exc))
            return

        if result.duplicates_in_file:
            QMessageBox.warning(
                self, "Dubbletter i filen",
                "Följande ObjectID förekommer flera gånger i filen:\n"
                + "\n".join(result.duplicates_in_file[:20]),
            )

        msg = (f"Filen innehåller {result.total_rows} rader.\n"
               f"Att importera: {result.imported}  |  Hoppar över: {result.skipped}")
        reply = QMessageBox.question(
            self, "Bekräfta import", msg,
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Ok:
            return

        removed_ids = self._import_svc.commit_import(visits, path, result)

        if removed_ids:
            reply2 = QMessageBox.question(
                self, "Besök som saknas i importen",
                f"{len(removed_ids)} besök finns i databasen men inte i den nya filen.\n"
                "Vill du ta bort dem? (De ersätts med tomrum i rutterna.)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply2 == QMessageBox.StandardButton.Yes:
                self._import_svc.remove_visits_not_in_import(removed_ids)
                # Rebuild routes that had those visits
                routes = self._db.load_all_routes(
                    {v.id: v for v in self._db.load_all_visits()})
                for r in routes:
                    self._routes[r.id] = r
                    self._route_scene.rebuild_route(r)

        # Reload everything
        self._load_data()
        QMessageBox.information(
            self, "Import klar",
            f"Importerade {result.imported} besök."
        )

    # ------------------------------------------------------------------
    # Settings dialog
    # ------------------------------------------------------------------

    @Slot()
    def _on_open_settings(self):
        dlg = SettingsDialog(self._settings, self._api_key, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            previous_debug_mode = bool(self._settings.debug_mode)
            self._settings = dlg.get_settings()
            self._apply_visit_color_palette()
            self._recalc.set_settings(self._settings)
            new_key = dlg.get_api_key()
            if new_key != self._api_key:
                self._api_key = new_key
                _save_api_key(new_key)
                self._travel_svc.set_api_key(new_key)
                if self._map_window is not None:
                    self._map_window.close()
                    self._map_window = None
            self._db.save_settings(self._settings)
            # Sync toolbar widgets
            self._on_font_size_changed(self._settings.font_size)
            idx = self._mode_combo.findData(self._settings.default_travel_mode)
            if idx >= 0:
                self._mode_combo.setCurrentIndex(idx)
            self._extra_time_auto_action.setChecked(bool(self._settings.extra_time_auto_place))
            self._extra_time_spin.setValue(max(0, int(self._settings.extra_time_minutes)))
            self._get_travel_status().configure_file_logging(
                self._settings.file_logging_enabled,
                self._settings.file_logging_retention_days,
            )

            if self._settings.debug_mode:
                if not previous_debug_mode:
                    self._get_travel_status().log_debug("Debug mode enabled")
                self._run_debug_integrity_scan("Settings")
            elif previous_debug_mode:
                self._get_travel_status().log_debug("Debug mode disabled")
                self._clear_debug_integrity_marks()

    def _apply_visit_color_palette(self):
        VisitItem.set_color_palette(self._settings.visit_ribbon_color_map())
        for scene in (getattr(self, "_route_scene", None), getattr(self, "_pool_scene", None)):
            if scene is None:
                continue
            for item in scene.items():
                if isinstance(item, VisitItem):
                    item.update()

    # ------------------------------------------------------------------
    # State export / import
    # ------------------------------------------------------------------

    @Slot()
    def _on_export_state(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Spara tillstånd", "planning_state.json",
            "JSON-filer (*.json)"
        )
        if not path:
            return
        state = self._db.export_state()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        QMessageBox.information(self, "Export klar", f"Tillståndet sparades till\n{path}")

    @Slot()
    def _on_import_state(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Ladda tillstånd", "", "JSON-filer (*.json)"
        )
        if not path:
            return
        reply = QMessageBox.question(
            self, "Bekräfta",
            "Detta ersätter all nuvarande data (besök och rutter). Fortsätta?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)
            self._db.import_state(state)
            self._load_data()
            QMessageBox.information(self, "Import klar", "Tillståndet laddades.")
        except Exception as exc:
            QMessageBox.critical(self, "Fel vid import", str(exc))

    # ------------------------------------------------------------------
    # Excel export
    # ------------------------------------------------------------------

    @Slot()
    def _on_export_excel(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Exportera Excel", "export.xlsx",
            "Excel-filer (*.xlsx)"
        )
        if not path:
            return
        try:
            self._write_excel_export(path)
            QMessageBox.information(self, "Export klar", f"Exporterat till\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Exportfel", str(exc))

    @Slot()
    def _on_export_pdf(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Exportera PDF", "export.pdf",
            "PDF-filer (*.pdf)"
        )
        if not path:
            return
        try:
            self._write_pdf_export(path)
            QMessageBox.information(self, "Export klar", f"Exporterat till\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Exportfel", str(exc))

    def _to_excel_sheet_name(self, route_name: str, used_names: set[str]) -> str:
        base = re.sub(r"[\\/*?:\[\]]", "_", (route_name or "").strip())
        if not base:
            base = "Rutt"
        base = base[:31]
        candidate = base
        suffix_num = 2
        while candidate in used_names:
            suffix = f" ({suffix_num})"
            candidate = f"{base[:31 - len(suffix)]}{suffix}"
            suffix_num += 1
        used_names.add(candidate)
        return candidate

    def _write_excel_export(self, path: str):
        import openpyxl
        from openpyxl.styles import Font, PatternFill
        from openpyxl.utils import get_column_letter

        wb = openpyxl.Workbook()

        # Remove default sheet so only route sheets are exported.
        wb.remove(wb.active)

        header_labels = ["Start", "Slut", "Namn", "Adress", "Insatser"]
        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill(fill_type="solid", fgColor="4F81BD")
        odd_fill = PatternFill(fill_type="solid", fgColor="DCE6F1")
        even_fill = PatternFill(fill_type="solid", fgColor="EDF2F9")

        used_sheet_names: set[str] = set()
        routes = sorted(self._routes.values(), key=lambda r: r.display_order)

        for route in routes:
            sheet_name = self._to_excel_sheet_name(route.name, used_sheet_names)
            ws = wb.create_sheet(sheet_name)
            ws.append(header_labels)

            for col_idx in range(1, len(header_labels) + 1):
                cell = ws.cell(row=1, column=col_idx)
                cell.font = header_font
                cell.fill = header_fill

            for data_idx, entry in enumerate(route.sorted_entries(), start=1):
                ws.append([
                    _display_time(entry.start_time),
                    _display_time(entry.end_time),
                    entry.display_name,
                    entry.display_address,
                    entry.display_insatser,
                ])
                row_idx = data_idx + 1
                row_fill = odd_fill if data_idx % 2 == 1 else even_fill
                for col_idx in range(1, len(header_labels) + 1):
                    ws.cell(row=row_idx, column=col_idx).fill = row_fill

            ws.auto_filter.ref = f"A1:E{max(1, ws.max_row)}"

            for col_idx in range(1, len(header_labels) + 1):
                max_len = 0
                for row_idx in range(1, ws.max_row + 1):
                    value = ws.cell(row=row_idx, column=col_idx).value
                    text = "" if value is None else str(value)
                    if len(text) > max_len:
                        max_len = len(text)
                ws.column_dimensions[get_column_letter(col_idx)].width = max(14, max_len + 2)

        if not routes:
            ws = wb.create_sheet("Rutter")
            ws.append(header_labels)
            for col_idx in range(1, len(header_labels) + 1):
                cell = ws.cell(row=1, column=col_idx)
                cell.font = header_font
                cell.fill = header_fill
            ws.auto_filter.ref = "A1:E1"

        wb.save(path)

    def _write_pdf_export(self, path: str):
        writer = QPdfWriter(path)
        writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        writer.setPageOrientation(QPageLayout.Orientation.Portrait)
        writer.setResolution(144)

        painter = QPainter()
        if not painter.begin(writer):
            raise RuntimeError("Kunde inte skapa PDF-export.")

        try:
            page_rect = writer.pageLayout().paintRectPixels(writer.resolution())
            margin = 36
            left = page_rect.left() + margin
            top = page_rect.top() + margin
            right = page_rect.right() - margin
            bottom = page_rect.bottom() - margin
            page_w = right - left
            page_h = bottom - top

            route_name_font = QFont("Segoe UI", 11, QFont.Weight.Bold)
            body_font = QFont("Segoe UI", 9)
            small_font = QFont("Segoe UI", 8)

            color_map = {
                key: QColor(value)
                for key, value in self._settings.visit_ribbon_color_map().items()
            }

            column_gap = 18
            columns_per_page = 2
            column_w = int((page_w - (column_gap * (columns_per_page - 1))) / columns_per_page)

            header_h = 98
            visit_h = 72
            travel_h = 44
            empty_h = 30
            extra_h = 30
            block_gap = 8
            content_pad = 10
            left_strip_w = 6

            routes = sorted(self._routes.values(), key=lambda r: r.display_order)
            if not routes:
                painter.setFont(route_name_font)
                painter.setPen(QPen(QColor("#000000")))
                painter.drawText(
                    QRectF(left, top, page_w, 40),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    "Inga rutter att exportera",
                )
                return

            def draw_route_column(
                route: Route,
                x: int,
                y: int,
                items_subset: list[tuple[str, object]],
                continued: bool = False,
            ):
                col_h = page_h

                painter.fillRect(QRectF(x, y, column_w, col_h), QColor(COLOR_ROUTE_COLUMN_BG))
                painter.setPen(QPen(QColor("#B0BEC5"), 1))
                painter.drawRect(QRectF(x, y, column_w, col_h))

                painter.fillRect(QRectF(x, y, column_w, header_h), QColor(COLOR_HEADER_BG))
                painter.setPen(QPen(QColor("#CFD8DC"), 1))
                painter.drawLine(x, y + header_h, x + column_w, y + header_h)

                painter.setPen(QPen(QColor("#212121")))
                painter.setFont(route_name_font)
                painter.drawText(
                    QRectF(x + content_pad, y + 6, column_w - (content_pad * 2), 28),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    f"{route.name or 'Rutt'}{' (forts.)' if continued else ''}",
                )

                painter.setFont(small_font)
                painter.setPen(QPen(QColor("#546E7A")))
                painter.drawText(
                    QRectF(x + content_pad, y + 36, column_w - (content_pad * 2), 16),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    "Anteckningar:",
                )
                painter.setPen(QPen(QColor("#37474F")))
                painter.drawText(
                    QRectF(x + content_pad, y + 52, column_w - (content_pad * 2), 40),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                    route.notes.strip() if route.notes.strip() else "-",
                )

                row_y = y + header_h + block_gap
                for item_type, item in items_subset:
                    if item_type == "travel":
                        seg = item
                        is_verklig = bool(
                            not seg.is_custom
                            and seg.calculated_minutes is not None
                            and not seg.api_failed
                        )
                        travel_bg = QColor("#E8F5E9") if is_verklig else QColor(COLOR_TRAVEL_BG)
                        travel_border = QColor("#2E7D32") if is_verklig else QColor(COLOR_TRAVEL_BORDER)
                        travel_text = QColor("#1B5E20") if is_verklig else QColor("#5D4037")
                        travel_value = QColor("#2E7D32") if is_verklig else QColor("#1565C0")

                        painter.fillRect(
                            QRectF(x + content_pad, row_y, column_w - (content_pad * 2), travel_h),
                            travel_bg,
                        )
                        painter.setPen(QPen(travel_border, 1))
                        painter.drawRect(QRectF(x + content_pad, row_y, column_w - (content_pad * 2), travel_h))

                        mode_label = {
                            TravelMode.CAR: "Bil",
                            TravelMode.BIKE: "Cykel",
                            TravelMode.WALK: "Gång",
                        }.get(seg.mode, seg.mode)
                        if seg.is_custom:
                            source_label = "Redigerad restid"
                        elif is_verklig:
                            source_label = "Verklig restid"
                        else:
                            source_label = "Standard restid"

                        painter.setFont(small_font)
                        painter.setPen(QPen(travel_text))
                        painter.drawText(
                            QRectF(x + content_pad + 8, row_y + 5, column_w - (content_pad * 2) - 90, 16),
                            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                            source_label,
                        )
                        painter.drawText(
                            QRectF(x + content_pad + 8, row_y + 22, column_w - (content_pad * 2) - 90, 16),
                            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                            f"Restid ({mode_label})",
                        )
                        painter.setFont(body_font)
                        painter.setPen(QPen(travel_value))
                        painter.drawText(
                            QRectF(x + column_w - content_pad - 80, row_y + 9, 72, 24),
                            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                            f"{seg.travel_minutes} min",
                        )
                        row_y += travel_h + block_gap
                        continue

                    if item_type == "empty":
                        esp = item
                        painter.fillRect(
                            QRectF(x + content_pad, row_y, column_w - (content_pad * 2), empty_h),
                            QColor(COLOR_EMPTY_BG),
                        )
                        painter.setPen(QPen(QColor(COLOR_EMPTY_BORDER), 1))
                        painter.drawRect(QRectF(x + content_pad, row_y, column_w - (content_pad * 2), empty_h))
                        painter.setFont(small_font)
                        painter.setPen(QPen(QColor("#0D47A1")))
                        painter.drawText(
                            QRectF(x + content_pad + 8, row_y, column_w - (content_pad * 2) - 16, empty_h),
                            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                            f"Lucka: {esp.duration_minutes} min",
                        )
                        row_y += empty_h + block_gap
                        continue

                    if item_type == "extra":
                        painter.fillRect(
                            QRectF(x + content_pad, row_y, column_w - (content_pad * 2), extra_h),
                            QColor("#E8F5E9"),
                        )
                        painter.setPen(QPen(QColor("#2E7D32"), 1))
                        painter.drawRect(QRectF(x + content_pad, row_y, column_w - (content_pad * 2), extra_h))
                        painter.setFont(small_font)
                        painter.setPen(QPen(QColor("#1B5E20")))
                        painter.drawText(
                            QRectF(x + content_pad + 8, row_y, column_w - (content_pad * 2) - 16, extra_h),
                            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                            f"Extratid: {self._settings.extra_time_minutes} min",
                        )
                        row_y += extra_h + block_gap
                        continue

                    entry = item
                    painter.fillRect(QRectF(x + content_pad, row_y, column_w - (content_pad * 2), visit_h), QColor(COLOR_VISIT_BG))

                    strip_color = color_map.get(entry.display_color or "", QColor("#2B2B2B"))
                    painter.fillRect(
                        QRectF(x + content_pad, row_y, left_strip_w, visit_h),
                        strip_color,
                    )

                    painter.setPen(QPen(QColor(COLOR_VISIT_BORDER), 1))
                    painter.drawRect(QRectF(x + content_pad, row_y, column_w - (content_pad * 2), visit_h))

                    text_x = x + content_pad + left_strip_w + 8
                    text_w = column_w - (content_pad * 2) - left_strip_w - 86

                    painter.setFont(body_font)
                    painter.setPen(QPen(QColor("#212121")))
                    painter.drawText(
                        QRectF(text_x, row_y + 6, text_w, 22),
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                        entry.display_name,
                    )

                    painter.setFont(small_font)
                    painter.setPen(QPen(QColor("#424242")))
                    painter.drawText(
                        QRectF(text_x, row_y + 28, text_w, 18),
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                        entry.display_address,
                    )
                    painter.drawText(
                        QRectF(text_x, row_y + 46, text_w, 16),
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                        entry.display_insatser,
                    )

                    painter.setFont(body_font)
                    painter.setPen(QPen(QColor("#0D47A1")))
                    visit_duration = max(0, _t2m(entry.end_time) - _t2m(entry.start_time))
                    painter.drawText(
                        QRectF(x + column_w - content_pad - 72, row_y + 8, 68, 22),
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                        _display_time(entry.start_time),
                    )
                    painter.drawText(
                        QRectF(x + column_w - content_pad - 72, row_y + 34, 68, 22),
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                        _display_time(entry.end_time),
                    )
                    painter.setFont(small_font)
                    painter.setPen(QPen(QColor("#1565C0")))
                    painter.drawText(
                        QRectF(x + column_w - content_pad - 72, row_y + 52, 68, 16),
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                        f"{visit_duration} min",
                    )

                    row_y += visit_h + block_gap

            available_h = page_h - header_h - content_pad - block_gap

            route_chunk_map: dict[int, list[list[tuple[str, object]]]] = {}
            for route in routes:
                entries = route.sorted_entries()

                route_items: list[tuple[str, object]] = []
                for idx, entry in enumerate(entries):
                    if idx > 0:
                        prev = entries[idx - 1]
                        if self._settings.show_extra_time_blocks and route.extra_time_for_entry(entry.id):
                            route_items.append(("extra", route.extra_time_for_entry(entry.id)))
                        if self._settings.show_travel_blocks:
                            seg = route.travel_segment_between(prev.id, entry.id)
                            if seg:
                                route_items.append(("travel", seg))
                        if self._settings.show_space_blocks:
                            esp = route.empty_space_between(prev.id, entry.id)
                            if esp and esp.duration_minutes > 0:
                                route_items.append(("empty", esp))
                    route_items.append(("visit", entry))

                if not route_items:
                    route_chunk_map[route.id] = [[]]
                    continue

                current_chunk: list[tuple[str, object]] = []
                current_h = 0
                route_chunks: list[list[tuple[str, object]]] = []
                for item_type, item in route_items:
                    item_h = visit_h
                    if item_type == "travel":
                        item_h = travel_h
                    elif item_type == "empty":
                        item_h = empty_h
                    elif item_type == "extra":
                        item_h = extra_h

                    block_h = item_h + block_gap
                    if current_chunk and current_h + block_h > available_h:
                        route_chunks.append(current_chunk)
                        current_chunk = []
                        current_h = 0
                    current_chunk.append((item_type, item))
                    current_h += block_h

                if current_chunk:
                    route_chunks.append(current_chunk)

                route_chunk_map[route.id] = route_chunks

            first_route = True
            for route in routes:
                chunks = route_chunk_map.get(route.id, [[]])

                if not first_route:
                    writer.newPage()
                first_route = False

                page_local_chunk_idx = 0
                while page_local_chunk_idx < len(chunks):
                    if page_local_chunk_idx > 0:
                        writer.newPage()

                    col_x_left = left
                    draw_route_column(
                        route,
                        col_x_left,
                        top,
                        chunks[page_local_chunk_idx],
                        continued=(page_local_chunk_idx > 0),
                    )
                    page_local_chunk_idx += 1

                    if page_local_chunk_idx < len(chunks):
                        col_x_right = left + (column_w + column_gap)
                        draw_route_column(
                            route,
                            col_x_right,
                            top,
                            chunks[page_local_chunk_idx],
                            continued=(page_local_chunk_idx > 0),
                        )
                        page_local_chunk_idx += 1
        finally:
            painter.end()

    # ------------------------------------------------------------------
    # Auto-save
    # ------------------------------------------------------------------

    @Slot()
    def _on_autosave(self):
        self._db.save_settings(self._settings)
        dirty = self._autosave.dirty_routes()
        for route_id in dirty:
            route = self._routes.get(route_id)
            if route:
                self._db.save_route(route)
        self._autosave.clear()

    # ------------------------------------------------------------------
    # Travel status window
    # ------------------------------------------------------------------

    def _get_travel_status(self) -> TravelStatusDialog:
        if self._travel_status is None:
            self._travel_status = TravelStatusDialog(self)
            self._travel_status.configure_file_logging(
                self._settings.file_logging_enabled,
                self._settings.file_logging_retention_days,
            )
        return self._travel_status

    @Slot()
    def _show_travel_status(self):
        dlg = self._get_travel_status()
        dlg.show()
        dlg.raise_()

    # ------------------------------------------------------------------
    # Visit selection + pair highlighting
    # ------------------------------------------------------------------

    @Slot(int, int)
    def _on_route_visit_selected(self, route_id: int, entry_id: int):
        self._route_scene.set_selected_entry(route_id, entry_id)
        self._pool_scene.set_selected_visit(None)
        route = self._routes.get(route_id)
        if route:
            entry = next((e for e in route.entries if e.id == entry_id), None)
            if entry and entry.visit_id:
                self._selected_visit_id = entry.visit_id
                self._sync_map_selection()
                self._highlight_pair_for_visit(entry.visit_id)
                return
        self._selected_visit_id = None
        self._sync_map_selection()
        self._highlight_pair_for_visit(None)

    @Slot(int)
    def _on_pool_visit_selected(self, visit_id: int):
        self._pool_scene.set_selected_visit(visit_id)
        self._route_scene.set_selected_entry(None, None)
        self._selected_visit_id = visit_id
        self._sync_map_selection()
        self._highlight_pair_for_visit(visit_id)

    def _highlight_pair_for_visit(self, visit_id: Optional[int]):
        partner_id = self._pairs.get(visit_id) if visit_id else None
        self._route_scene.highlight_pair(None, None)
        self._pool_scene.highlight_pair(partner_id)
        if partner_id:
            # Find partner in routes
            for route in self._routes.values():
                for entry in route.entries:
                    if entry.visit_id == partner_id:
                        self._route_scene.highlight_pair(route.id, entry.id)

    # ------------------------------------------------------------------
    # Close event
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        if self._map_window is not None:
            self._map_window.close()
        self._autosave.flush_now()
        self._db.save_settings(self._settings)
        self._db.close()
        super().closeEvent(event)
