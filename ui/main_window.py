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
    QToolBar, QLabel, QSpinBox, QComboBox, QPushButton,
    QScrollArea, QFileDialog, QMessageBox, QInputDialog, QDialog,
    QGroupBox, QScrollBar, QSizePolicy, QFormLayout, QDialogButtonBox, QLineEdit,
)

from domain.models import (
    Route, RouteEntry, TravelSegment, EmptySpace, Visit,
    Settings, TravelMode, TravelTimeState,
)
from domain.constants import VISIT_WIDTH, COLUMN_SPACING
from services.persistence_service import PersistenceService
from services.excel_import_service import ExcelImportService
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
        self._default_template_address = "Angereds Torg 5, 424 65 Angered"
        self._default_templates = [
            {"name": "Kontor", "address": self._default_template_address, "duration_minutes": 10},
            {"name": "Uppstart", "address": self._default_template_address, "duration_minutes": 30},
            {"name": "Rast", "address": self._default_template_address, "duration_minutes": 40},
            {"name": "Avslut", "address": self._default_template_address, "duration_minutes": 20},
        ]

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
        self._travel_debounce_timers: dict[tuple[int, int, int], QTimer] = {}
        self._failed_fallback_keys: set[tuple[str, str, str]] = set()

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

        self._show_travel_action = QAction("Restid", self)
        self._show_travel_action.setCheckable(True)
        self._show_travel_action.setChecked(True)
        self._show_travel_action.toggled.connect(self._on_block_visibility_changed)
        tb.addAction(self._show_travel_action)

        self._show_space_action = QAction("Lucka", self)
        self._show_space_action.setCheckable(True)
        self._show_space_action.setChecked(True)
        self._show_space_action.toggled.connect(self._on_block_visibility_changed)
        tb.addAction(self._show_space_action)

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

        act_integrity = QAction("Tidsintegritet", self)
        act_integrity.triggered.connect(self._on_time_integrity)
        tb.addAction(act_integrity)

        act_reset_all = QAction("Rensa allt", self)
        act_reset_all.triggered.connect(self._on_reset_all)
        tb.addAction(act_reset_all)

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
        s.entry_color_changed.connect(self._on_entry_color_changed)
        s.entry_remove_requested.connect(self._on_entry_remove_requested)
        s.empty_remove.connect(self._on_empty_remove)
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

        routes = self._db.load_all_routes(self._visits)
        self._routes = {r.id: r for r in routes}

        self._compute_pairs()
        self._build_filter_bar()

        # Pool: visits NOT placed in any route
        placed = self._db.get_placed_visit_ids()
        pool_visits = [v for v in visits if v.id not in placed]

        self._pool_scene.load(self._default_templates, pool_visits)
        self._route_scene.load_routes(routes)

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
            min_start = _t2m(prev.end_time) + travel

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
            base_start = _t2m(prev.end_time) + travel
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

    @Slot(str, str, str, str)
    def _on_travel_time_error(self, from_addr: str, to_addr: str, mode: str, error_msg: str):
        self._get_travel_status().log_error(from_addr, to_addr, mode, error_msg)
        self._failed_fallback_keys.add((from_addr, to_addr, mode))
        self._set_segments_lookup_state(from_addr, to_addr, mode, is_calculating=False,
                                        api_failed=True, api_error=error_msg)

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
        for route in self._routes.values():
            self._recalc.recalculate(route)
            self._route_scene.rebuild_route(route)
        self._autosave.flush_now()

    @Slot()
    def _on_block_visibility_changed(self):
        self._route_scene.set_block_visibility(
            self._show_travel_action.isChecked(),
            self._show_space_action.isChecked(),
        )

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
            self._settings = dlg.get_settings()
            new_key = dlg.get_api_key()
            if new_key != self._api_key:
                self._api_key = new_key
                _save_api_key(new_key)
                self._travel_svc.set_api_key(new_key)
            self._db.save_settings(self._settings)
            # Sync toolbar widgets
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
                    _display_time(entry.start_time),
                    _display_time(entry.end_time),
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
