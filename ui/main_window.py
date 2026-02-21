"""MainWindow – top-level coordinator for the route planning application."""

from __future__ import annotations

import configparser
import json
import os
from typing import Optional

from PySide6.QtCore import Qt, Slot, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QToolBar, QLabel, QSpinBox, QComboBox, QCheckBox, QPushButton,
    QScrollArea, QFileDialog, QMessageBox, QInputDialog, QDialog,
    QGroupBox, QScrollBar, QSizePolicy,
)

from domain.models import (
    Route, RouteEntry, TravelSegment, EmptySpace, Visit,
    Settings, OfficeTemplate, TravelMode,
)
from domain.constants import VISIT_WIDTH, COLUMN_SPACING
from services.persistence_service import PersistenceService
from services.excel_import_service import ExcelImportService
from services.travel_time_service import TravelTimeService
from services.auto_save_manager import AutoSaveManager
from controllers.route_recalculation_engine import RouteRecalculationEngine, _t2m, _m2t
from controllers.route_layout_engine import RouteLayoutEngine
from ui.scenes.route_scene import RouteScene
from ui.scenes.pool_scene import PoolScene
from ui.views.route_view import RouteView
from ui.views.pool_view import PoolView
from ui.flow_layout import FlowLayout
from ui.dialogs.settings_dialog import SettingsDialog
from ui.dialogs.travel_status_dialog import TravelStatusDialog


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
        self._api_key: str = _load_api_key()
        self._office_template: OfficeTemplate = self._db.load_office_template()

        # In-memory collections
        self._visits: dict[int, Visit] = {}
        self._routes: dict[int, Route] = {}

        # Services / controllers
        self._travel_svc = TravelTimeService(self._db, self._api_key, self._settings)
        self._recalc = RouteRecalculationEngine(self._db, self._travel_svc)
        self._layout_engine = RouteLayoutEngine(self._settings.font_size)
        self._autosave = AutoSaveManager(parent=self)
        self._autosave.save_requested.connect(self._on_autosave)

        # Import / travel status helpers
        self._import_svc = ExcelImportService(self._db)
        self._travel_status: Optional[TravelStatusDialog] = None

        # Paired visit index: visit_id → partner_visit_id
        self._pairs: dict[int, int] = {}

        # Build UI
        self._build_ui()
        self._load_data()
        self._connect_travel_signals()

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
        filter_label_row.addStretch()
        filter_bar_vbox.addLayout(filter_label_row)

        self._filter_cb_container = QWidget()
        self._filter_cb_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._filter_layout = FlowLayout(self._filter_cb_container, h_spacing=6, v_spacing=4)
        self._filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_bar_vbox.addWidget(self._filter_cb_container)

        self._filter_checkboxes: dict[str, QCheckBox] = {}
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

        # Import
        act_import = QAction("Importera Excel", self)
        act_import.triggered.connect(self._on_import_excel)
        tb.addAction(act_import)

        # Export
        act_export_state = QAction("Spara planering", self)
        act_export_state.triggered.connect(self._on_export_state)
        tb.addAction(act_export_state)

        act_import_state = QAction("Öppna planering", self)
        act_import_state.triggered.connect(self._on_import_state)
        tb.addAction(act_import_state)

        act_export_excel = QAction("Exportera Excel", self)
        act_export_excel.triggered.connect(self._on_export_excel)
        tb.addAction(act_export_excel)

        tb.addSeparator()

        # Add route
        act_add_route = QAction("＋ Ny rutt", self)
        act_add_route.triggered.connect(self._on_add_route)
        tb.addAction(act_add_route)

        tb.addSeparator()

        # Travel API status
        act_travel_log = QAction("API-logg", self)
        act_travel_log.triggered.connect(self._show_travel_status)
        tb.addAction(act_travel_log)

        tb.addSeparator()

        # Default travel mode
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

        tb.addSeparator()

        # Minimum time controls
        tb.addWidget(QLabel(" Min. tid: "))
        self._min_time_spin = QSpinBox()
        self._min_time_spin.setRange(0, 60)
        self._min_time_spin.setValue(self._settings.minimum_time_between_visits)
        self._min_time_spin.setSuffix(" min")
        self._min_time_spin.valueChanged.connect(self._on_min_time_changed)
        tb.addWidget(self._min_time_spin)

        act_apply_min = QAction("Applicera mintid", self)
        act_apply_min.triggered.connect(self._on_apply_min_time)
        tb.addAction(act_apply_min)

        act_strip = QAction("Rensa luckor", self)
        act_strip.triggered.connect(self._on_strip_empty_space)
        tb.addAction(act_strip)

        tb.addSeparator()

        act_settings = QAction("Inställningar", self)
        act_settings.triggered.connect(self._on_open_settings)
        tb.addAction(act_settings)

    def _wire_route_scene(self):
        s = self._route_scene
        s.route_renamed.connect(self._on_route_renamed)
        s.route_notes_changed.connect(self._on_route_notes_changed)
        s.route_delete_requested.connect(self._on_route_delete)
        s.entry_dropped.connect(self._on_entry_dropped)
        s.entry_moved.connect(self._on_entry_moved)
        s.entry_time_edit.connect(self._on_entry_time_edit)
        s.entry_duration_changed.connect(self._on_entry_duration_changed)
        s.travel_mode_changed.connect(self._on_travel_mode_changed)
        s.travel_minutes_edit.connect(self._on_travel_minutes_edit)
        s.travel_restore.connect(self._on_travel_restore)
        s.visit_selected.connect(self._on_route_visit_selected)

    def _wire_pool_scene(self):
        self._pool_scene.office_edit_requested.connect(self._on_office_edit)
        self._pool_scene.entry_returned_to_pool.connect(self._on_entry_returned)
        self._pool_scene.visit_selected.connect(self._on_pool_visit_selected)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_data(self):
        visits = self._db.load_all_visits()
        self._visits = {v.id: v for v in visits}

        routes = self._db.load_all_routes(self._visits)
        self._routes = {r.id: r for r in routes}

        self._compute_pairs()
        self._build_filter_bar()

        # Pool: visits NOT placed in any route
        placed = self._db.get_placed_visit_ids()
        pool_visits = [v for v in visits if v.id not in placed]

        self._pool_scene.load(self._office_template, pool_visits)
        self._route_scene.load_routes(routes)

    def _compute_pairs(self):
        """Find DUBBELGÅNG 1 / DUBBELGÅNG 2 pairs."""
        self._pairs.clear()
        cands: dict[str, list[Visit]] = {}
        for v in self._visits.values():
            ins = v.insatser.upper()
            if "DUBBELGÅNG 1" in ins or "DUBBELGÅNG 2" in ins:
                key = f"{v.name.strip().lower()}|{v.address.strip().lower()}"
                cands.setdefault(key, []).append(v)

        for group in cands.values():
            if len(group) < 2:
                continue
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    a, b = group[i], group[j]
                    if abs(_t2m(a.default_start) - _t2m(b.default_start)) <= 20:
                        self._pairs[a.id] = b.id
                        self._pairs[b.id] = a.id

    def _build_filter_bar(self):
        # Clear existing checkboxes (hide first to avoid flash)
        for cb in self._filter_checkboxes.values():
            cb.hide()
            cb.deleteLater()
        # Purge dead layout items
        self._filter_layout._items.clear()
        self._filter_checkboxes.clear()

        # Collect unique Insatser tokens
        tags: set[str] = set()
        for v in self._visits.values():
            for tag in v.insatser.split(","):
                t = tag.strip()
                if t:
                    tags.add(t)

        for tag in sorted(tags):
            cb = QCheckBox(tag, self._filter_cb_container)
            cb.setChecked(True)
            cb.toggled.connect(self._on_filter_changed)
            self._filter_checkboxes[tag] = cb
            self._filter_layout.addWidget(cb)

        self._filter_layout.invalidate()
        self._filter_cb_container.updateGeometry()

    # ------------------------------------------------------------------
    # Filter
    # ------------------------------------------------------------------

    @Slot()
    def _on_filter_changed(self):
        active = {tag for tag, cb in self._filter_checkboxes.items() if cb.isChecked()}
        if len(active) == len(self._filter_checkboxes):
            active = set()  # all checked → no filter
        self._route_scene.apply_filter(active)
        self._pool_scene.apply_filter(active)

    # ------------------------------------------------------------------
    # Route management
    # ------------------------------------------------------------------

    @Slot()
    def _on_add_route(self):
        name, ok = QInputDialog.getText(self, "Ny rutt", "Ruttnamn:")
        if not ok or not name.strip():
            return
        order = max((r.display_order for r in self._routes.values()), default=-1) + 1
        route = self._db.create_route(name.strip(), order)
        self._routes[route.id] = route
        self._route_scene.add_route(route)

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

        if dtype == "pool_visit":
            visit_id = data["visit_id"]
            visit = self._visits.get(visit_id)
            if not visit:
                return
            entry = RouteEntry(
                id=None, route_id=route_id, visit_id=visit_id,
                position=0,
                start_time=visit.default_start,
                end_time=visit.default_end,
                visit=visit,
            )
            self._recalc.add_entry_to_route(route, entry,
                                             self._settings.default_travel_mode)
            self._pool_scene.remove_visit(visit_id)
            self._request_travel_for_new_entry(route, entry)
            self._route_scene.rebuild_route(route)

        elif dtype == "office":
            entry = RouteEntry(
                id=None, route_id=route_id, visit_id=None,
                position=0,
                start_time="08:00", end_time="08:30",
                is_office_instance=True,
                office_name=data.get("name", "Kontor"),
                office_address=data.get("address", ""),
            )
            self._recalc.add_entry_to_route(route, entry,
                                             self._settings.default_travel_mode)
            self._route_scene.rebuild_route(route)

        elif dtype == "route_entry":
            # Move entry from one route to another (or reorder within same)
            entry_id = data["entry_id"]
            src_route_id = data["source_route_id"]
            if src_route_id == route_id:
                return  # same route: handled by up/down
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
            self._recalc.add_entry_to_route(route, entry,
                                             self._settings.default_travel_mode)
            self._route_scene.rebuild_route(src_route)
            self._route_scene.rebuild_route(route)

        self._autosave.mark_dirty(route_id)

    @Slot(int)
    def _on_entry_returned(self, entry_id: int):
        """Visit dragged back to pool."""
        for route in self._routes.values():
            entry = next((e for e in route.entries if e.id == entry_id), None)
            if entry:
                if entry.visit_id and entry.visit_id in self._visits:
                    v = self._visits[entry.visit_id]
                    # Restore default times
                    v_restored = Visit(
                        id=v.id, object_id=v.object_id, name=v.name,
                        address=v.address, street=v.street,
                        default_start=v.default_start, default_end=v.default_end,
                        insatser=v.insatser, color=v.color, raw_data=v.raw_data,
                        full_address=v.full_address,
                    )
                    self._recalc.remove_entry_from_route(route, entry,
                                                          replace_with_empty=True)
                    self._pool_scene.add_visit(v_restored)
                    self._route_scene.rebuild_route(route)
                    self._autosave.mark_dirty(route.id)
                return

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

        # Calculate minimum allowed start
        min_start = 0
        if entry_idx > 0:
            prev = entries[entry_idx - 1]
            seg = route.travel_segment_between(prev.id, entry.id)
            travel = seg.travel_minutes if seg else 0
            min_start = _t2m(prev.end_time) + travel

        text, ok = QInputDialog.getText(
            self, "Redigera tid",
            f"Starttid för {entry.display_name}\n"
            f"(format HH:MM, tidigast {_m2t(min_start)}):",
            text=entry.start_time,
        )
        if not ok or not text.strip():
            return

        import re
        m = re.match(r"(\d{1,2}):(\d{2})", text.strip())
        if not m:
            QMessageBox.warning(self, "Fel format", "Ange tid som HH:MM")
            return

        new_start = int(m.group(1)) * 60 + int(m.group(2))
        if new_start <= min_start and entry_idx > 0:
            QMessageBox.warning(
                self, "Ogiltig tid",
                f"Starttiden måste vara efter {_m2t(min_start)}."
            )
            return

        duration = _t2m(entry.end_time) - _t2m(entry.start_time)
        entry.start_time = _m2t(new_start)
        entry.end_time = _m2t(new_start + duration)

        self._recalc.recalculate_after_entry_time_change(route, entry)
        self._route_scene.rebuild_route(route)
        self._autosave.mark_dirty(route_id)

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
        entry.end_time = _m2t(_t2m(entry.start_time) + new_dur)
        self._recalc.recalculate_after_entry_time_change(route, entry)
        self._route_scene.rebuild_route(route)
        self._autosave.mark_dirty(route_id)

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
        self._recalc.recalculate(route)
        self._route_scene.rebuild_route(route)
        self._autosave.mark_dirty(route_id)

        # Trigger async API lookup with new mode
        from_entry = next((e for e in route.entries if e.id == seg.from_entry_id), None)
        to_entry = next((e for e in route.entries if e.id == seg.to_entry_id), None)
        if from_entry and to_entry:
            self._travel_svc.request_travel_time(
                from_entry.display_address, to_entry.display_address, mode
            )

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
            value=seg.travel_minutes, min=0, max=300,
        )
        if ok:
            self._recalc.set_custom_travel_minutes(route, seg, val)
            self._route_scene.rebuild_route(route)
            self._autosave.mark_dirty(route_id)

    @Slot(int, int)
    def _on_travel_restore(self, route_id: int, seg_id: int):
        route = self._routes.get(route_id)
        if not route:
            return
        seg = next((s for s in route.travel_segments if s.id == seg_id), None)
        if not seg:
            return
        self._recalc.restore_calculated_travel(route, seg)
        self._route_scene.rebuild_route(route)
        self._autosave.mark_dirty(route_id)

    # ------------------------------------------------------------------
    # Travel time async result
    # ------------------------------------------------------------------

    @Slot(str, str, str, int)
    def _on_travel_time_ready(self, from_addr: str, to_addr: str,
                               mode: str, minutes: int):
        """Update any route segments that use this pair."""
        for route in self._routes.values():
            changed = False
            for seg in route.travel_segments:
                if seg.is_custom:
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
                    changed = True
            if changed:
                self._recalc.recalculate(route)
                self._route_scene.rebuild_route(route)
                self._autosave.mark_dirty(route.id)

    def _connect_travel_signals(self):
        self._travel_svc.travel_time_ready.connect(self._on_travel_time_ready)
        self._travel_svc.request_logged.connect(
            lambda t: self._get_travel_status().log_request(t))
        self._travel_svc.response_logged.connect(
            lambda t: self._get_travel_status().log_response(t))
        self._travel_svc.error_occurred.connect(
            lambda f, t, m, e: self._get_travel_status().log_error(f, t, m, e))
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
        mode = self._settings.default_travel_mode
        if idx > 0:
            prev = entries[idx - 1]
            self._travel_svc.request_travel_time(
                prev.api_address, new_entry.api_address, mode)
        if idx < len(entries) - 1:
            nxt = entries[idx + 1]
            self._travel_svc.request_travel_time(
                new_entry.api_address, nxt.api_address, mode)

    # ------------------------------------------------------------------
    # Office template
    # ------------------------------------------------------------------

    @Slot()
    def _on_office_edit(self):
        """Called after the user double-click-edited the template in the scene."""
        self._db.save_office_template(self._office_template)
        self._pool_scene.update_template(self._office_template)

    # ------------------------------------------------------------------
    # Minimum time policy
    # ------------------------------------------------------------------

    @Slot(int)
    def _on_min_time_changed(self, value: int):
        self._settings.minimum_time_between_visits = value
        self._autosave.mark_dirty()

    @Slot()
    def _on_apply_min_time(self):
        for route in self._routes.values():
            self._recalc.apply_minimum_time(
                route, self._settings.minimum_time_between_visits)
            self._route_scene.rebuild_route(route)
        self._autosave.flush_now()

    @Slot()
    def _on_strip_empty_space(self):
        for route in self._routes.values():
            self._recalc.strip_extra_empty_space(route)
            self._route_scene.rebuild_route(route)
        self._autosave.flush_now()

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
            self._settings = dlg.get_settings()
            new_key = dlg.get_api_key()
            if new_key != self._api_key:
                self._api_key = new_key
                _save_api_key(new_key)
                self._travel_svc.set_api_key(new_key)
            self._db.save_settings(self._settings)
            # Sync toolbar widgets
            self._min_time_spin.setValue(self._settings.minimum_time_between_visits)
            self._on_font_size_changed(self._settings.font_size)
            idx = self._mode_combo.findData(self._settings.default_travel_mode)
            if idx >= 0:
                self._mode_combo.setCurrentIndex(idx)

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

    def _write_excel_export(self, path: str):
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment

        wb = openpyxl.Workbook()

        # Sheet 1: Routes
        ws_routes = wb.active
        ws_routes.title = "Rutter"
        header_font = Font(bold=True)
        ws_routes.append(["Rutt", "Anteckningar", "Typ", "Namn", "Adress",
                           "Starttid", "Sluttid", "Insatser"])
        ws_routes.row_dimensions[1].font = header_font

        for route in sorted(self._routes.values(), key=lambda r: r.display_order):
            for entry in route.sorted_entries():
                ws_routes.append([
                    route.name,
                    route.notes,
                    "Kontor" if entry.is_office_instance else "Besök",
                    entry.display_name,
                    entry.display_address,
                    entry.start_time,
                    entry.end_time,
                    entry.display_insatser,
                ])

        # Sheet 2: Visit pool
        ws_pool = wb.create_sheet("Besökslista")
        ws_pool.append(["Gata", "Namn", "Adress", "Starttid", "Sluttid", "Insatser", "Färg"])
        ws_pool.row_dimensions[1].font = header_font

        placed = self._db.get_placed_visit_ids()
        pool_visits = [v for v in self._visits.values() if v.id not in placed]
        for v in sorted(pool_visits, key=lambda x: (x.street, x.default_start)):
            ws_pool.append([
                v.street, v.name, v.address,
                v.default_start, v.default_end,
                v.insatser, v.color or "",
            ])

        wb.save(path)

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
                self._highlight_pair_for_visit(entry.visit_id)
                return
        self._highlight_pair_for_visit(None)

    @Slot(int)
    def _on_pool_visit_selected(self, visit_id: int):
        self._pool_scene.set_selected_visit(visit_id)
        self._route_scene.set_selected_entry(None, None)
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
        self._autosave.flush_now()
        self._db.save_settings(self._settings)
        self._db.close()
        super().closeEvent(event)
